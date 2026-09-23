"""Student lookup tool: fixed REST API, fuzzy name matching, strict field filtering."""
import asyncio
import logging
import time

import httpx
from rapidfuzz import fuzz

from ..config import env

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_student_details",
        "description": (
            "Look up a student's school details by first name and surname. "
            "Only call this after reading the name back to the caller and getting their confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string", "description": "Student's first name"},
                "last_name": {"type": "string", "description": "Student's surname"},
                "name_confirmed_by_user": {
                    "type": "boolean",
                    "description": "True only if the caller explicitly confirmed the name you read back.",
                },
            },
            "required": ["first_name", "last_name", "name_confirmed_by_user"],
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
    def __init__(self, cfg: dict, session, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.session = session
        self.base_url = (env(cfg["base_url_env"]) or "").rstrip("/")
        self.api_key = env(cfg.get("api_key_env"))
        self.client = client or httpx.AsyncClient(timeout=cfg.get("timeout_ms", 3000) / 1000)
        self.field_map = cfg["field_map"]
        self.never = {f.lower() for f in cfg.get("never_speak_fields", [])}
        self.speakable = [f for f in cfg["speakable_fields"] if f.lower() not in self.never]
        self._cache: dict[tuple, tuple[float, dict]] = {}

    async def lookup(self, first_name: str = "", last_name: str = "", name_confirmed_by_user: bool = False, **_):
        first, last = (first_name or "").strip(), (last_name or "").strip()
        if self.cfg.get("caller_auth_required", True) and not self.session.authenticated:
            return {"status": "caller_not_verified",
                    "message": "Ask the caller for their staff PIN and call verify_caller before looking up students."}
        if not first or not last:
            return {"status": "missing_name", "message": "Ask the caller for both the first name and the surname."}
        if self.cfg.get("require_name_confirmation", True) and name_confirmed_by_user is not True:
            return {"status": "needs_confirmation",
                    "message": f"Read the name '{first} {last}' back to the caller and ask them to confirm first."}
        if not self.base_url:
            return {"status": "error", "message": "The student records service is not configured."}

        key = (first.lower(), last.lower())
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.cfg.get("cache_ttl_seconds", 300):
            return cached[1]

        try:
            records = await self._search(first, last)
            if not records and self.cfg.get("fallback_search_by_last_name"):
                records = await self._search("", last)
        except StudentAPIError as e:
            log.warning("Student API error: %s", e)
            return {"status": "error", "message": "The student records service is not responding right now."}

        result = self._build_result(self._rank(records, first, last), first, last)
        if result["status"] in ("found", "multiple_matches", "not_found"):
            self._cache[key] = (time.monotonic(), result)
        return result

    async def _search(self, first: str, last: str) -> list[dict]:
        params = {k: v.format(first_name=first, last_name=last) for k, v in self.cfg["query_params"].items()}
        params = {k: v for k, v in params.items() if v}
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.base_url + self.cfg["endpoint"]
        retries = self.cfg.get("retries", 2)
        last_error = None
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

    def _get(self, record: dict, field: str):
        return record.get(self.field_map.get(field, field))

    def _full_name(self, record: dict) -> str:
        return f"{self._get(record, 'first_name') or ''} {self._get(record, 'last_name') or ''}".strip()

    def _rank(self, records: list[dict], first: str, last: str) -> list[dict]:
        target = f"{first} {last}".lower()
        fz = self.cfg.get("fuzzy_match", {})
        scored = []
        for rec in records:
            name = self._full_name(rec).lower()
            if not name:
                continue
            if fz.get("enabled", True):
                score = fuzz.token_sort_ratio(target, name) / 100
                if score >= fz.get("min_score", 0.85):
                    scored.append((score, rec))
            elif name == target:
                scored.append((1.0, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    def _speakable(self, record: dict) -> dict:
        out = {"full_name": self._full_name(record)}
        for field in self.speakable:
            value = self._get(record, field)
            if value not in (None, ""):
                out[field] = value
        return out

    def _build_result(self, matches: list[dict], first: str, last: str) -> dict:
        if not matches:
            return {"status": "not_found", "message": f"No student named {first} {last} was found."}
        if len(matches) == 1:
            return {"status": "found", "student": self._speakable(matches[0])}
        limit = self.cfg.get("max_matches_to_read", 3)
        if len(matches) > limit:
            return {"status": "too_many_matches", "count": len(matches),
                    "message": "Too many students match. Ask for the class and division."}
        options = [{"full_name": self._full_name(m), "class": self._get(m, "class"),
                    "division": self._get(m, "division")} for m in matches]
        return {"status": "multiple_matches", "options": options,
                "message": "Ask the caller which student they mean, using class and division.",
                "students": [self._speakable(m) for m in matches]}
