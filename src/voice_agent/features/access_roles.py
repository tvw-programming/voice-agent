"""Feature 7: role-based access to student records.

What it means: after the PIN identifies a staff member, their *role* decides
which students they may hear about.

  admin, principal, clerk (office staff)  -> any student in the school
  teacher                                 -> only students in their own classes

A teacher's classes are stored in data/staff.json, e.g. ["8-A", "8-B", "10"]
("10" means every division of class 10). Roles and what they allow are set in
config.json -> "access".

When a restricted staff member asks about a student outside their classes the
tool answers "not_in_your_classes", worded so it does not reveal whether that
student exists.

Staff commands (in staff_pins.py):
  python -m voice_agent.features.staff_pins add --name "Meena Kale" --role teacher --classes 8-A,8-B
  python -m voice_agent.features.staff_pins set-classes S03 8-A,9-C
  python -m voice_agent.features.staff_pins set-role S03 clerk
"""
from . import class_search

ALL = "all"
OWN = "own_classes"

DEFAULT_ROLES = {"admin": ALL, "principal": ALL, "clerk": ALL, "teacher": OWN}


class Scope:
    """The classes one staff member may look up. `everything` means no restriction."""

    def __init__(self, everything: bool, classes: list[str] | None = None):
        self.everything = everything
        self.entries: list[tuple[str, str | None]] = []
        for raw in classes or []:
            parsed = parse_class_entry(raw)
            if parsed:
                self.entries.append(parsed)

    def allows(self, cls, div) -> bool:
        if self.everything:
            return True
        c = class_search.normalize_class(cls)
        d = class_search.normalize_division(div) if div not in (None, "") else None
        for ec, ed in self.entries:
            if c == ec and (ed is None or d == ed):
                return True
        return False

    def allows_request(self, cls: str | None, div: str | None) -> bool:
        """Can this request possibly return a permitted student? (checked before calling the API)"""
        if self.everything or not cls:
            return True
        return any(cls == ec and (div is None or ed is None or div == ed) for ec, ed in self.entries)

    def describe(self) -> str:
        if self.everything:
            return "all classes"
        if not self.entries:
            return "no classes"
        return ", ".join(f"{c}-{d}" if d else f"class {c}" for c, d in self.entries)


def parse_class_entry(raw: str) -> tuple[str, str | None] | None:
    """'8-A' -> ('8', 'A'); '10' -> ('10', None); 'UKG A' -> ('UKG', 'A')."""
    text = str(raw).strip()
    if not text:
        return None
    parts = text.replace("-", " ").split()
    div = None
    if len(parts) > 1 and len(parts[-1]) == 1 and parts[-1].isalpha():
        div = parts[-1].upper()
        parts = parts[:-1]
    cls = class_search.normalize_class(" ".join(parts))
    return (cls, div) if cls else None


def scope_for(session, access_cfg: dict | None) -> Scope:
    """Work out the scope for the verified staff member on this call."""
    access_cfg = access_cfg or {}
    if not access_cfg.get("enabled", True):
        return Scope(True)
    role = (getattr(session, "staff_role", None) or "").lower()
    if not role or getattr(session, "staff_id", None) == "shared":
        return Scope(True)  # no staff identity (PIN check off) or legacy shared PIN: behaves as before
    roles = {k.lower(): v for k, v in (access_cfg.get("roles") or DEFAULT_ROLES).items()}
    rule = roles.get(role, access_cfg.get("unknown_role", OWN))
    if rule == ALL:
        return Scope(True)
    return Scope(False, getattr(session, "staff_classes", None) or [])


def filter_records(records: list[dict], scope: Scope, get) -> list[dict]:
    """Keep only the records this staff member may hear about. `get(record, field)` reads mapped fields."""
    if scope.everything:
        return list(records)
    return [r for r in records if scope.allows(get(r, "class"), get(r, "division"))]


def refusal(scope: Scope) -> dict:
    return {"status": "not_in_your_classes",
            "message": (f"No student in this staff member's classes ({scope.describe()}) matches that request. "
                        "Explain that teachers can look up only students in their own classes, and that the "
                        "school office can help with others. Do not say whether the student exists.")}
