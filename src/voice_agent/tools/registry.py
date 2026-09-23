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
    def __init__(self, pin: str | None, max_attempts: int = 3):
        self._pin = spoken_digits(pin) if pin else None
        self.max_attempts = max_attempts
        self.attempts = 0
        self.authenticated = False

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
            return {"status": "verified"}
        left = self.max_attempts - self.attempts
        return {"status": "wrong_pin", "attempts_left": left}


class ToolRegistry:
    def __init__(self, cfg: dict, session: Session, http_client=None):
        self.session = session
        scfg = cfg["tools"]["student_lookup"]
        self.student_enabled = scfg.get("enabled", True)
        self.auth_required = scfg.get("caller_auth_required", True)
        self.student = student.StudentLookup(scfg, session, client=http_client) if self.student_enabled else None

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
                return await self.student.lookup(**args)
        except TypeError as e:
            return {"status": "error", "message": f"Bad tool arguments: {e}"}
        return {"status": "error", "message": f"Unknown tool {name}"}


def build_session(cfg: dict) -> Session:
    scfg = cfg["tools"]["student_lookup"]
    return Session(env(scfg.get("staff_pin_env")), scfg.get("max_pin_attempts", 3))
