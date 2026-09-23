"""Feature 6: one PIN per staff member.

* Staff are stored in data/staff.json. PINs are never stored, only
  HMAC-SHA256(STAFF_PIN_SECRET, pin), so a copied staff file is useless without
  the secret in .env. PINs are unique, so a PIN identifies exactly one person.
* StaffSession replaces the shared-PIN Session: same interface
  (authenticated, verify()) plus staff_id / staff_name.
* 3 wrong attempts per call lock that call; 10 failures in 10 minutes across
  all calls lock lookups for 15 minutes.
* PinInterceptor catches the PIN in code *before* the LLM sees it, so PINs
  never reach a cloud model or the conversation history.
* Legacy: if the staff file has no active staff and STAFF_PIN is set, the old
  shared PIN still works and is logged as staff "shared".

Admin commands:
  python -m voice_agent.features.staff_pins add --name "Sunita Patil" --role clerk
  python -m voice_agent.features.staff_pins add --name "Meena Kale" --role teacher --classes 8-A,8-B
  python -m voice_agent.features.staff_pins list
  python -m voice_agent.features.staff_pins set-role S02 clerk
  python -m voice_agent.features.staff_pins set-classes S02 8-A,9-C
  python -m voice_agent.features.staff_pins reset-pin S01
  python -m voice_agent.features.staff_pins disable S01
  python -m voice_agent.features.staff_pins enable S01
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]

# Copied from text.py on purpose (features are self-contained).
_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4",
    "paanch": "5", "panch": "5", "chhe": "6", "che": "6", "saat": "7", "aath": "8", "nau": "9",
    # Devanagari digits and words
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
    "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5", "छह": "6", "सात": "7",
    "आठ": "8", "नौ": "9", "शून्य": "0",
}


def spoken_digits(text: str) -> str:
    """'four three two one' / '4 3 2 1' / 'char teen do ek' -> '4321'."""
    out = []
    for token in re.findall(r"[a-zA-Z]+|[ऀ-ॿ]+|\d", str(text).lower()):
        if token.isdigit() and len(token) == 1 and token.isascii():
            out.append(token)
        elif token in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[token])
        elif all(ch in _DIGIT_WORDS for ch in token):  # Devanagari digit run
            out.extend(_DIGIT_WORDS[ch] for ch in token)
    return "".join(out)


def _env(name: str | None) -> str | None:
    return (os.environ.get(name) or None) if name else None


# ---- staff store ------------------------------------------------------------

class StaffStore:
    def __init__(self, path: str | Path, secret: str | None):
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = ROOT / self.path
        self.secret = (secret or "").encode()
        self.staff: list[dict] = []
        self.load()

    def load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.staff = data.get("staff", [])
        else:
            self.staff = []

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"staff": self.staff}, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def pin_hash(self, pin: str) -> str:
        if not self.secret:
            raise RuntimeError("STAFF_PIN_SECRET is not set in .env")
        return hmac.new(self.secret, spoken_digits(pin).encode(), hashlib.sha256).hexdigest()

    def active(self) -> list[dict]:
        return [s for s in self.staff if s.get("active", True)]

    def find_by_pin(self, pin: str) -> dict | None:
        if not self.secret or not pin:
            return None
        digest = self.pin_hash(pin)
        match = None
        for s in self.active():
            if hmac.compare_digest(s.get("pin_hash", ""), digest):
                match = s  # keep looping: constant work regardless of position
        return match

    def get(self, staff_id: str) -> dict | None:
        return next((s for s in self.staff if s["id"].lower() == staff_id.lower()), None)

    def _next_id(self) -> str:
        nums = [int(s["id"][1:]) for s in self.staff if re.fullmatch(r"S\d+", s["id"])]
        return f"S{(max(nums) + 1) if nums else 1:02d}"

    def _new_unique_pin(self, length: int) -> str:
        used = {s.get("pin_hash") for s in self.staff}
        for _ in range(1000):
            pin = "".join(secrets.choice("0123456789") for _ in range(length))
            if len(set(pin)) > 1 and self.pin_hash(pin) not in used:  # no 000000 / 111111
                return pin
        raise RuntimeError("Could not generate a unique PIN; increase pin_length")

    def add(self, name: str, role: str = "clerk", pin: str | None = None, length: int = 6,
            classes: list[str] | None = None) -> tuple[dict, str]:
        pin = spoken_digits(pin) if pin else self._new_unique_pin(length)
        if len(pin) != length:
            raise ValueError(f"PIN must be exactly {length} digits")
        if any(s.get("pin_hash") == self.pin_hash(pin) for s in self.staff):
            raise ValueError("That PIN is already used by another staff member")
        entry = {"id": self._next_id(), "name": name, "role": role, "classes": list(classes or []),
                 "pin_hash": self.pin_hash(pin), "active": True,
                 "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.staff.append(entry)
        self.save()
        return entry, pin

    def reset_pin(self, staff_id: str, length: int = 6) -> str:
        s = self.get(staff_id)
        if not s:
            raise KeyError(staff_id)
        s["pin_hash"] = ""  # free the old PIN before choosing a new one
        pin = self._new_unique_pin(length)
        s["pin_hash"] = self.pin_hash(pin)
        self.save()
        return pin

    def set_role(self, staff_id: str, role: str):
        s = self.get(staff_id)
        if not s:
            raise KeyError(staff_id)
        s["role"] = role
        self.save()

    def set_classes(self, staff_id: str, classes: list[str]):
        s = self.get(staff_id)
        if not s:
            raise KeyError(staff_id)
        s["classes"] = list(classes)
        self.save()

    def set_active(self, staff_id: str, active: bool):
        s = self.get(staff_id)
        if not s:
            raise KeyError(staff_id)
        s["active"] = active
        self.save()


# ---- lockout shared by all calls in this process ---------------------------

class GlobalLockout:
    def __init__(self, max_failures: int = 10, window_seconds: int = 600, lock_seconds: int = 900,
                 clock=time.monotonic):
        self.max_failures, self.window, self.lock_seconds = max_failures, window_seconds, lock_seconds
        self.clock = clock
        self.failures: deque[float] = deque()
        self.locked_until = 0.0
        self._lock = threading.Lock()

    def is_locked(self) -> bool:
        return self.clock() < self.locked_until

    def record_failure(self):
        with self._lock:
            now = self.clock()
            self.failures.append(now)
            while self.failures and now - self.failures[0] > self.window:
                self.failures.popleft()
            if len(self.failures) >= self.max_failures:
                self.locked_until = now + self.lock_seconds
                self.failures.clear()
                log.warning("Too many wrong PINs: lookups locked for %d seconds", self.lock_seconds)


# ---- per-call session -------------------------------------------------------

class StaffSession:
    """Per-call verification state. Drop-in replacement for registry.Session."""

    def __init__(self, store: StaffStore | None, legacy_pin: str | None = None, max_attempts: int = 3,
                 lockout: GlobalLockout | None = None, max_lookups: int = 20):
        self.store = store
        self._legacy_pin = spoken_digits(legacy_pin) if legacy_pin else None
        self.max_attempts = max_attempts
        self.lockout = lockout or GlobalLockout()
        self.attempts = 0
        self.authenticated = False
        self.staff_id: str | None = None
        self.staff_name: str | None = None
        self.staff_role: str | None = None         # Feature 7 (access_roles.py)
        self.staff_classes: list[str] = []
        self.max_lookups = max_lookups
        self.lookups = 0
        self.audit = None  # set by the registry; receives verify events

    @property
    def uses_staff_file(self) -> bool:
        return bool(self.store and self.store.active())

    def verify(self, pin_spoken: str) -> dict:
        if self.authenticated:
            return {"status": "verified", "staff_name": self.staff_name}
        if self.lockout.is_locked():
            self._audit("locked", "global_lockout")
            return {"status": "locked", "message": "Too many wrong PINs recently. Lookups are paused; try again later."}
        if not self.uses_staff_file and not self._legacy_pin:
            return {"status": "error", "message": "Caller verification is not configured on this system."}
        if self.attempts >= self.max_attempts:
            self._audit("locked", "call_attempts")
            return {"status": "locked", "message": "Too many wrong PIN attempts. Student lookups are disabled for this call."}
        self.attempts += 1
        pin = spoken_digits(pin_spoken)
        staff = None
        if self.uses_staff_file:
            staff = self.store.find_by_pin(pin)
        elif self._legacy_pin and hmac.compare_digest(pin, self._legacy_pin):
            staff = {"id": "shared", "name": "Staff", "role": "admin"}
        if staff:
            self.authenticated = True
            self.staff_id, self.staff_name = staff["id"], staff["name"]
            self.staff_role = staff.get("role", "clerk")
            self.staff_classes = list(staff.get("classes") or [])
            self._audit("verify_ok", "verified")
            return {"status": "verified", "staff_name": self.staff_name}
        self.lockout.record_failure()
        left = self.max_attempts - self.attempts
        self._audit("verify_fail", "wrong_pin")
        return {"status": "wrong_pin", "attempts_left": left}

    def allow_lookup(self) -> bool:
        if self.lookups >= self.max_lookups:
            return False
        self.lookups += 1
        return True

    def _audit(self, event: str, status: str):
        if self.audit is not None:
            try:
                self.audit.record(event=event, staff_id=self.staff_id, staff_name=self.staff_name, status=status)
            except Exception as e:  # noqa: BLE001
                log.error("Audit write failed for %s: %s", event, e)


def build_staff_session(cfg: dict, lockout: GlobalLockout | None = None) -> StaffSession:
    scfg = cfg.get("staff", {})
    lcfg = cfg["tools"]["student_lookup"]
    store = None
    if scfg.get("enabled", True):
        store = StaffStore(scfg.get("file", "data/staff.json"), _env(scfg.get("secret_env", "STAFF_PIN_SECRET")))
    return StaffSession(
        store, legacy_pin=_env(lcfg.get("staff_pin_env")), max_attempts=lcfg.get("max_pin_attempts", 3),
        lockout=lockout or GlobalLockout(scfg.get("global_max_failures", 10), scfg.get("global_window_seconds", 600),
                                         scfg.get("global_lock_seconds", 900)),
        max_lookups=lcfg.get("max_lookups_per_call", 20))


# ---- PIN interception (before the LLM) --------------------------------------

_ASKED_FOR_PIN = re.compile(r"\bpin\b|पिन", re.I)


class PinInterceptor:
    """If the caller isn't verified and the agent just asked for the PIN, verify in code.

    Returns the text the LLM should see instead of the caller's words, or None to pass through.
    """

    def __init__(self, session, min_digits: int = 4, max_digits: int = 8):
        self.session = session
        self.min_digits, self.max_digits = min_digits, max_digits

    def intercept(self, user_text: str, last_agent_text: str | None) -> str | None:
        if getattr(self.session, "authenticated", True):
            return None
        if not last_agent_text or not _ASKED_FOR_PIN.search(last_agent_text):
            return None
        digits = spoken_digits(user_text)
        if not self.min_digits <= len(digits) <= self.max_digits:
            return None
        result = self.session.verify(digits)
        status = result.get("status")
        if status == "verified":
            who = result.get("staff_name") or "staff"
            return (f"[The caller spoke their staff PIN. It was checked by the system: verified as {who}. "
                    f"Greet them by name and ask how you can help. Do not call verify_caller.]")
        if status == "wrong_pin":
            return (f"[The caller spoke a staff PIN. It was checked by the system: wrong PIN, "
                    f"{result.get('attempts_left', 0)} attempts left. Ask them to try again.]")
        return f"[The caller spoke a staff PIN. System result: {result.get('message', status)}]"


# ---- admin CLI --------------------------------------------------------------

def _cli(argv=None):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    scfg = cfg.get("staff", {})
    length = scfg.get("pin_length", 6)

    ap = argparse.ArgumentParser(prog="python -m voice_agent.features.staff_pins",
                                 description="Manage staff PINs for Vani")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add a staff member and print their new PIN")
    a.add_argument("--name", required=True)
    a.add_argument("--role", default="clerk", help="admin, principal, clerk or teacher (see config.json -> access)")
    a.add_argument("--classes", default="", help="teacher's classes, e.g. 8-A,8-B or 10")
    a.add_argument("--pin", help=f"choose the PIN ({length} digits) instead of a random one")
    sub.add_parser("list", help="list staff (never shows PINs)")
    for cmd in ("reset-pin", "disable", "enable"):
        sub.add_parser(cmd).add_argument("staff_id")
    r = sub.add_parser("set-role")
    r.add_argument("staff_id")
    r.add_argument("role")
    c = sub.add_parser("set-classes")
    c.add_argument("staff_id")
    c.add_argument("classes", help="comma-separated, e.g. 8-A,8-B; empty string for none")
    args = ap.parse_args(argv)

    secret = _env(scfg.get("secret_env", "STAFF_PIN_SECRET"))
    if not secret:
        print("STAFF_PIN_SECRET is not set in .env. Generate one with:\n"
              "  python -c \"import secrets; print(secrets.token_hex(32))\"")
        return 1
    store = StaffStore(scfg.get("file", "data/staff.json"), secret)
    if args.cmd == "add":
        classes = [c.strip() for c in args.classes.split(",") if c.strip()]
        entry, pin = store.add(args.name, args.role, args.pin, length, classes)
        where = f", classes {', '.join(classes)}" if classes else ""
        print(f"Added {entry['id']} {entry['name']} ({entry['role']}{where}). PIN: {pin}")
        if args.role.lower() == "teacher" and not classes:
            print("Note: a teacher with no classes can't look up any student. Use set-classes.")
        print("Give this PIN to the staff member now. It cannot be shown again.")
    elif args.cmd == "list":
        for s in store.staff:
            classes = ",".join(s.get("classes") or []) or "-"
            print(f"{s['id']:5} {'active  ' if s.get('active', True) else 'disabled'} {s['role']:10} "
                  f"{classes:14} {s['name']}")
        if not store.staff:
            print("No staff yet. Add one with: add --name \"Full Name\" --role clerk")
    elif args.cmd == "set-role":
        store.set_role(args.staff_id, args.role)
        print(f"{args.staff_id} is now {args.role}")
    elif args.cmd == "set-classes":
        classes = [c.strip() for c in args.classes.split(",") if c.strip()]
        store.set_classes(args.staff_id, classes)
        print(f"{args.staff_id} classes: {', '.join(classes) or 'none'}")
    elif args.cmd == "reset-pin":
        print(f"New PIN for {args.staff_id}: {store.reset_pin(args.staff_id, length)}")
    else:
        store.set_active(args.staff_id, args.cmd == "enable")
        print(f"{args.staff_id} {'enabled' if args.cmd == 'enable' else 'disabled'}")
    return 0


def _main():
    try:
        return _cli()
    except (ValueError, KeyError) as e:
        print(f"Error: {e.args[0] if e.args else e}" + (" (no such staff id)" if isinstance(e, KeyError) else ""))
        return 1


if __name__ == "__main__":
    sys.exit(_main())
