import hmac
import logging

from ..config import env
from ..text import spoken_digits
from . import student

log = logging.getLogger(__name__)

VERIFY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "verify_caller",
        "description": "Verify the caller with the staff PIN they spoke. Required before student lookups.",
        "parameters": {
            "type": "object",
            "properties": {"pin": {"type": "string", "description": "The PIN digits exactly as the caller said them"}},
            "required": ["pin"],
        },
    },
}


class Session:
    """Legacy shared-PIN session (kept for compatibility; see features/staff_pins.py for per-staff PINs)."""

    def __init__(self, pin: str | None, max_attempts: int = 3):
        self._pin = spoken_digits(pin) if pin else None
        self.max_attempts = max_attempts
        self.attempts = 0
        self.authenticated = False
        self.staff_id = None
        self.staff_name = None
        self.staff_role = None
        self.staff_classes = []

    def verify(self, pin_spoken: str) -> dict:
        if self.authenticated:
            return {"status": "verified"}
        if not self._pin:
            return {"status": "error", "message": "Caller verification is not configured on this system."}
        if self.attempts >= self.max_attempts:
            return {"status": "locked", "message": "Too many wrong PIN attempts. Student lookups are disabled for this call."}
        self.attempts += 1
        if hmac.compare_digest(spoken_digits(pin_spoken), self._pin):
            self.authenticated = True
            self.staff_id, self.staff_name, self.staff_role = "shared", "Staff", "admin"
            return {"status": "verified"}
        left = self.max_attempts - self.attempts
        return {"status": "wrong_pin", "attempts_left": left}


class ToolRegistry:
    def __init__(self, cfg: dict, session, http_client=None, audit=None):
        """`audit` is a features.audit_log.CallAudit (or None to disable auditing)."""
        self.session = session
        self.audit = audit
        if audit is not None and hasattr(session, "audit"):
            session.audit = audit  # StaffSession records PIN attempts itself
        scfg = cfg["tools"]["student_lookup"]
        self.student_enabled = scfg.get("enabled", True)
        self.auth_required = scfg.get("caller_auth_required", True)
        self.student = (student.StudentLookup(scfg, session, client=http_client, access_cfg=cfg.get("access"))
                        if self.student_enabled else None)

    def set_llm_provider(self, name: str | None):
        if self.audit is not None:
            self.audit.llm_provider = name

    def schemas(self) -> list[dict]:
        out = []
        if self.student_enabled:
            if self.auth_required:
                out.append(VERIFY_SCHEMA)
            out.append(student.SCHEMA)
        return out

    async def call(self, name: str, args: dict) -> dict:
        try:
            if name == "verify_caller" and self.auth_required:
                return self.session.verify(str(args.get("pin", "")))
            if name == "get_student_details" and self.student:
                result = await self.student.lookup(**args)
                return self._audit_lookup(result)
        except TypeError as e:
            return {"status": "error", "message": f"Bad tool arguments: {e}"}
        return {"status": "error", "message": f"Unknown tool {name}"}

    def _audit_lookup(self, result: dict) -> dict:
        meta = result.pop("_audit", None)
        if meta is None or self.audit is None:
            return result
        try:
            self.audit.record(**meta)
        except Exception as e:  # noqa: BLE001
            log.error("Audit write failed: %s", e)
            if self.audit.fail_closed and result.get("status") in ("found", "multiple_matches", "confirm_match"):
                return {"status": "error",
                        "message": "The lookup could not be recorded, so the details can't be shared right now."}
        return result


def build_session(cfg: dict):
    """Per-staff PIN session (Feature 6). Falls back to the shared STAFF_PIN when no staff are enrolled."""
    from ..features.staff_pins import build_staff_session
    return build_staff_session(cfg)


def build_legacy_session(cfg: dict) -> Session:
    scfg = cfg["tools"]["student_lookup"]
    return Session(env(scfg.get("staff_pin_env")), scfg.get("max_pin_attempts", 3))
