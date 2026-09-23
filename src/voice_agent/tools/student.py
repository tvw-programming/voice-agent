"""Student lookup tool: fixed REST API, strict field filtering.

Uses the feature files:
  features/class_search.py  search by class, division, roll number   (Feature 2)
  features/spelling.py      names spelled letter by letter           (Feature 3)
  features/phonetic.py      sound-based matching of Indian names     (Feature 4)
  features/access_roles.py  teachers see only their own classes      (Feature 7)
Results carry a private "_audit" entry that the registry writes to the audit
log (Feature 5) and strips before the LLM sees the result.
"""
import asyncio
import logging
import time

import httpx

from ..config import env
from ..features import access_roles, class_search, phonetic, spelling

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_student_details",
        "description": (
            "Look up a student's school details. Search by first name and surname; or first name plus class "
            "and division; or class, division and roll number. Class and division narrow down duplicate names. "
            "If the caller spelled a name letter by letter, pass the letters exactly as heard in "
            "first_name_spelled / last_name_spelled. Only call this after reading the request back to the "
            "caller and getting their confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string", "description": "Student's first name"},
                "last_name": {"type": "string", "description": "Student's surname"},
                "first_name_spelled": {"type": "string",
                                       "description": "Letters of the first name exactly as the caller spelled them, e.g. 'P R I Y A'"},
                "last_name_spelled": {"type": "string",
                                      "description": "Letters of the surname exactly as the caller spelled them, e.g. 'K U L K A R N I'"},
                "class_name": {"type": "string", "description": "Class/standard, e.g. '8', 'eighth', 'UKG'"},
                "division": {"type": "string", "description": "Division/section letter, e.g. 'A'"},
                "roll_number": {"type": "string", "description": "Roll number, e.g. '12'"},
                "confirmed_by_user": {
                    "type": "boolean",
                    "description": "True only if the caller explicitly confirmed the name/class/roll you read back.",
                },
            },
            "required": ["confirmed_by_user"],
        },
    },
}


class StudentAPIError(Exception):
    pass


def _extract_records(payload) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "students", "items", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
            if isinstance(value, dict):
                return [value]
        return [payload] if payload else []
    return []


class StudentLookup:
    def __init__(self, cfg: dict, session, client: httpx.AsyncClient | None = None, access_cfg: dict | None = None):
        self.cfg = cfg
        self.access_cfg = access_cfg
        self.session = session
        self.base_url = (env(cfg["base_url_env"]) or "").rstrip("/")
        self.api_key = env(cfg.get("api_key_env"))
        self.client = client or httpx.AsyncClient(timeout=cfg.get("timeout_ms", 3000) / 1000)
        self.field_map = cfg["field_map"]
        self.never = {f.lower() for f in cfg.get("never_speak_fields", [])}
        self.speakable = [f for f in cfg["speakable_fields"] if f.lower() not in self.never]
        self.cs_cfg = cfg.get("class_search", {})
        self.ph_cfg = cfg.get("phonetic", {})
        self.word_divisions = self.cs_cfg.get("word_divisions", [])
        self._cache: dict[tuple, tuple[float, list[dict]]] = {}
        self.api_ms = 0

    # ---------------------------------------------------------------- main --
    async def lookup(self, first_name: str = "", last_name: str = "", first_name_spelled: str = "",
                     last_name_spelled: str = "", class_name: str = "", division: str = "", roll_number: str = "",
                     confirmed_by_user: bool = False, name_confirmed_by_user: bool = False, **_):
        confirmed = confirmed_by_user is True or name_confirmed_by_user is True
        first, last = (first_name or "").strip(), (last_name or "").strip()

        # Feature 3: spelled names win over what STT heard.
        spelled = False
        for which, raw in (("first", first_name_spelled), ("last", last_name_spelled)):
            if raw:
                name = spelling.assemble_spelling(raw)
                if not name:
                    return {"status": "spelling_unclear",
                            "message": "The spelled letters were unclear. Ask the caller to spell it again, "
                                       "using words like 'B for Bombay' for letters that sound alike."}
                spelled = True
                if which == "first":
                    first = name
                else:
                    last = name

        # Feature 2: normalise class, division, roll number.
        cls = class_search.normalize_class(class_name) if class_name else None
        div = class_search.normalize_division(division, self.word_divisions) if division else None
        roll = class_search.normalize_roll(roll_number) if roll_number else None
        if class_name and not cls:
            return {"status": "need_more_info", "message": f"'{class_name}' is not a class I recognise. Ask again."}

        if self.cfg.get("caller_auth_required", True) and not self.session.authenticated:
            return {"status": "caller_not_verified",
                    "message": "Ask the caller for their staff PIN before looking up students."}

        shape = class_search.search_shape(first, last, cls, div, roll)
        if shape is None:
            return {"status": "need_more_info",
                    "message": class_search.missing_info_message(first, last, cls, div, roll)}
        if shape == "class_roll" and self.cs_cfg.get("mode", "api") != "api":
            return {"status": "need_more_info",
                    "message": "Searching by roll number isn't available. Ask for the student's name."}

        request_text = self._describe_request(first, last, cls, div, roll)
        if self.cfg.get("require_name_confirmation", True) and not confirmed:
            readback = request_text
            if spelled:
                readback += " (spelled " + ", ".join(
                    spelling.letters_for_readback(n) for n in (first, last) if n) + ")"
            return {"status": "needs_confirmation",
                    "message": f"Read '{readback}' back to the caller and ask them to confirm first."}
        if not self.base_url:
            return {"status": "error", "message": "The student records service is not configured."}
        # Feature 7: a teacher asking about another class is refused before any API call.
        scope = access_roles.scope_for(self.session, self.access_cfg)
        if not scope.allows_request(cls, div):
            return self._with_audit(access_roles.refusal(scope),
                                    {"class": cls, "division": div, "roll_number": roll, "first_name": first,
                                     "last_name": last, "shape": shape}, [])
        if hasattr(self.session, "allow_lookup") and not self.session.allow_lookup():
            return {"status": "limit_reached",
                    "message": "The lookup limit for this call has been reached. Ask the caller to call again later."}

        query = {"first_name": first, "last_name": last, "class": cls, "division": div, "roll_number": roll,
                 "shape": shape, "spelled": spelled}
        self.api_ms = 0
        try:
            records = await self._candidates(shape, first, last, cls, div, roll)
        except StudentAPIError as e:
            log.warning("Student API error: %s", e)
            return self._with_audit({"status": "error", "message": "The student records service is not responding right now."},
                                    query, [])

        records = class_search.filter_records(records, self._get, cls, div, roll, self.word_divisions)
        matches = self._rank(records, first, last, strict=spelled)
        if not scope.everything:
            permitted = access_roles.filter_records(matches, scope, self._get)
            if matches and not permitted:
                return self._with_audit(access_roles.refusal(scope), query, [])
            matches = permitted
        result = self._build_result(matches, first, last, cls, div, roll, spelled)
        return self._with_audit(result, query, matches)

    # ---------------------------------------------------------- candidates --
    async def _candidates(self, shape, first, last, cls, div, roll) -> list[dict]:
        extra = self._class_params(cls, div, roll)
        if shape == "class_roll":
            return await self._search("", "", extra)
        if shape == "first_class":
            return await self._search(first, "", extra)
        records = await self._search(first, last, extra)
        if not records and self.cfg.get("fallback_search_by_last_name"):
            records = await self._search("", last, extra)
        if not records and self.ph_cfg.get("search_variants", True):
            # Feature 4: try spellings that sound the same (Desmukh -> Deshmukh).
            for variant in phonetic.spelling_variants(last, self.ph_cfg.get("max_variants", 6)):
                records = await self._search("", variant, extra)
                if records:
                    log.info("Found candidates via surname variant %r", variant)
                    break
        return records

    def _class_params(self, cls, div, roll) -> dict:
        if self.cs_cfg.get("mode", "api") != "api":
            return {}
        template = self.cs_cfg.get("query_params", {"class": "{class}", "division": "{division}",
                                                       "roll_number": "{roll_number}"})
        values = {"class": cls or "", "division": div or "", "roll_number": roll or ""}
        return {k: v.format(**values) for k, v in template.items()}

    async def _search(self, first: str, last: str, extra: dict | None = None) -> list[dict]:
        params = {k: v.format(first_name=first, last_name=last) for k, v in self.cfg["query_params"].items()}
        params.update(extra or {})
        params = {k: v for k, v in params.items() if v}
        key = tuple(sorted(params.items()))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.cfg.get("cache_ttl_seconds", 300):
            return cached[1]
        records = await self._fetch(params)
        self._cache[key] = (time.monotonic(), records)
        return records

    async def _fetch(self, params: dict) -> list[dict]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.base_url + self.cfg["endpoint"]
        retries = self.cfg.get("retries", 2)
        last_error = None
        start = time.monotonic()
        try:
            for attempt in range(retries + 1):
                try:
                    r = await self.client.request(self.cfg.get("method", "GET"), url, params=params, headers=headers)
                    if r.status_code == 404:
                        return []
                    if 400 <= r.status_code < 500:
                        raise StudentAPIError(f"HTTP {r.status_code} (check API key / parameters)")
                    r.raise_for_status()
                    return _extract_records(r.json())
                except StudentAPIError:
                    raise
                except (httpx.HTTPError, ValueError) as e:
                    last_error = e
                    if attempt < retries:
                        await asyncio.sleep(0.3 * (attempt + 1))
            raise StudentAPIError(str(last_error))
        finally:
            self.api_ms += int((time.monotonic() - start) * 1000)

    # ------------------------------------------------------------- ranking --
    def _get(self, record: dict, field: str):
        return record.get(self.field_map.get(field, field))

    def _full_name(self, record: dict) -> str:
        return f"{self._get(record, 'first_name') or ''} {self._get(record, 'last_name') or ''}".strip()

    def _rank(self, records: list[dict], first: str, last: str, strict: bool = False) -> list[dict]:
        """Feature 4: score by sound as well as spelling. Returns records best first."""
        if not first and not last:
            return list(records)  # class + roll search: nothing to match on
        min_total = self.ph_cfg.get("min_score", self.cfg.get("fuzzy_match", {}).get("min_score", 0.85))
        min_surname = self.ph_cfg.get("min_surname_score", 0.85)
        fuzzy_on = self.cfg.get("fuzzy_match", {}).get("enabled", True)
        scored = []
        for rec in records:
            rf, rl = str(self._get(rec, "first_name") or ""), str(self._get(rec, "last_name") or "")
            if not (rf or rl):
                continue
            if strict or not fuzzy_on:
                ok = ((not first or phonetic.keys_equal(first, rf) or first.lower() == rf.lower()) and
                      (not last or phonetic.keys_equal(last, rl) or last.lower() == rl.lower()))
                if ok:
                    scored.append((1.0, rec))
                continue
            total, surname = phonetic.name_score(first, last, rf, rl, self.ph_cfg.get("first_name_weight", 0.4))
            if total >= min_total and surname >= min_surname:
                scored.append((total, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    def _sounds_same(self, first: str, last: str, record: dict) -> bool:
        rf, rl = str(self._get(record, "first_name") or ""), str(self._get(record, "last_name") or "")
        return ((not first or phonetic.keys_equal(first, rf) or first.lower() == rf.lower()) and
                (not last or phonetic.keys_equal(last, rl) or last.lower() == rl.lower()))

    # ------------------------------------------------------------- results --
    def _speakable(self, record: dict) -> dict:
        out = {"full_name": self._full_name(record)}
        for field in self.speakable:
            value = self._get(record, field)
            if value not in (None, ""):
                out[field] = value
        return out

    def _label(self, record: dict) -> str:
        cls, div = self._get(record, "class"), self._get(record, "division")
        return f"{self._full_name(record)} ({cls}-{div})" if cls and div else self._full_name(record)

    def _describe_request(self, first, last, cls, div, roll) -> str:
        name = " ".join(p for p in (first, last) if p)
        where = class_search.describe(cls, div, roll)
        if name and where:
            return f"{name} in {where}"
        return name or where

    def _build_result(self, matches, first, last, cls, div, roll, spelled) -> dict:
        asked = self._describe_request(first, last, cls, div, roll)
        if not matches:
            result = {"status": "not_found", "message": f"No student matching {asked} was found."}
            if (first or last) and not spelled and self.cfg.get("suggest_spelling", True):
                result["suggest"] = "ask_to_spell"
                result["message"] += " Offer to take the surname letter by letter."
            return result
        if len(matches) == 1:
            m = matches[0]
            if (first or last) and not self._sounds_same(first, last, m) and self.ph_cfg.get("confirm_close_matches", True):
                # Feature 4 safety rule: close but not the same sound (Patel vs Patil).
                return {"status": "confirm_match",
                        "candidate": {"full_name": self._full_name(m), "class": self._get(m, "class"),
                                      "division": self._get(m, "division")},
                        "message": f"The closest match is {self._full_name(m)}. Ask the caller if that is the "
                                   "student they mean; if yes, look up that exact name again with confirmed_by_user true."}
            return {"status": "found", "student": self._speakable(m)}
        limit = self.cfg.get("max_matches_to_read", 3)
        if len(matches) > limit:
            return {"status": "too_many_matches", "count": len(matches),
                    "message": "Too many students match. Ask for the class and division."}
        options = [{"full_name": self._full_name(m), "class": self._get(m, "class"),
                    "division": self._get(m, "division")} for m in matches]
        return {"status": "multiple_matches", "options": options,
                "message": "Ask the caller which student they mean, using class and division.",
                "students": [self._speakable(m) for m in matches]}

    def _with_audit(self, result: dict, query: dict, matches: list[dict]) -> dict:
        status = result["status"]
        spoken = []
        if status == "found":
            spoken = [k for k in result["student"] if k != "full_name"]
            students = [self._label(matches[0])]
        elif status == "multiple_matches":
            spoken = sorted({k for s in result["students"] for k in s if k != "full_name"})
            students = [self._label(m) for m in matches]
        elif status == "confirm_match":
            students = [self._label(matches[0])]
            spoken = ["class", "division"]
        else:
            students = []
        result["_audit"] = {"event": "lookup", "query": query, "status": status, "match_count": len(matches),
                            "students": students, "fields_returned": spoken, "latency_ms": self.api_ms}
        return result
