"""Feature 7: teachers see only their own classes; office staff see everyone."""
import httpx
import pytest

from voice_agent.features.access_roles import Scope, parse_class_entry, scope_for
from voice_agent.features.staff_pins import StaffSession, StaffStore
from voice_agent.tools.registry import ToolRegistry

RECORDS = [
    {"first_name": "Aarav", "last_name": "Deshmukh", "class": "8", "division": "A", "roll_number": 12, "phone": "1"},
    {"first_name": "Aarav", "last_name": "Deshmukh", "class": "6", "division": "C", "roll_number": 4, "phone": "2"},
    {"first_name": "Priya", "last_name": "Kulkarni", "class": "10", "division": "B", "roll_number": 27, "phone": "3"},
]


def test_parse_class_entries():
    assert parse_class_entry("8-A") == ("8", "A")
    assert parse_class_entry("8 a") == ("8", "A")
    assert parse_class_entry("10") == ("10", None)
    assert parse_class_entry("UKG-B") == ("UKG", "B")
    assert parse_class_entry("nonsense") is None


def test_scope_rules():
    s = Scope(False, ["8-A", "10"])
    assert s.allows("8", "A") and s.allows("10", "C") and not s.allows("8", "B")
    assert s.allows_request("8", "A") and s.allows_request("10", None) and not s.allows_request("9", "A")
    assert Scope(False, []).describe() == "no classes"


class Who:
    def __init__(self, role, classes=(), staff_id="S01"):
        self.staff_role, self.staff_classes, self.staff_id = role, list(classes), staff_id


def test_roles_from_config(cfg):
    access = cfg["access"]
    assert scope_for(Who("clerk"), access).everything
    assert scope_for(Who("principal"), access).everything
    assert not scope_for(Who("teacher", ["8-A"]), access).everything
    assert not scope_for(Who("librarian"), access).everything          # unknown role -> own classes
    assert scope_for(Who("admin", staff_id="shared"), access).everything
    assert scope_for(Who("teacher"), {"enabled": False}).everything


def registry(cfg, monkeypatch, tmp_path, role, classes=()):
    monkeypatch.setenv("STUDENT_API_BASE_URL", "http://api.test")
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        p = request.url.params
        res = [r for r in RECORDS
               if (not p.get("first_name") or r["first_name"].lower() == p["first_name"].lower())
               and (not p.get("last_name") or r["last_name"].lower() == p["last_name"].lower())
               and (not p.get("class") or r["class"] == p["class"])
               and (not p.get("division") or r["division"] == p["division"])]
        return httpx.Response(200, json={"results": res})

    store = StaffStore(tmp_path / "staff.json", "secret")
    store.add("Meena Kale", role=role, pin="551234", classes=list(classes))
    session = StaffSession(store)
    assert session.verify("551234")["status"] == "verified"
    return ToolRegistry(cfg, session, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))), calls


async def lookup(reg, **args):
    return await reg.call("get_student_details", {"confirmed_by_user": True, **args})


async def test_teacher_sees_own_class(cfg, monkeypatch, tmp_path):
    reg, _ = registry(cfg, monkeypatch, tmp_path, "teacher", ["8-A"])
    r = await lookup(reg, first_name="Aarav", last_name="Deshmukh")
    # Two Aarav Deshmukhs exist, but only the 8-A one is this teacher's: no "which one?" question, no leak.
    assert r["status"] == "found" and r["student"]["class"] == "8"


async def test_teacher_refused_outside_classes_without_revealing(cfg, monkeypatch, tmp_path):
    reg, _ = registry(cfg, monkeypatch, tmp_path, "teacher", ["8-A"])
    r = await lookup(reg, first_name="Priya", last_name="Kulkarni")
    assert r["status"] == "not_in_your_classes"
    assert "Kulkarni" not in str(r) and "10" not in r["message"].split("(")[0]


async def test_teacher_class_request_refused_before_api_call(cfg, monkeypatch, tmp_path):
    reg, calls = registry(cfg, monkeypatch, tmp_path, "teacher", ["8-A"])
    r = await lookup(reg, class_name="10", division="B", roll_number="27")
    assert r["status"] == "not_in_your_classes" and calls == []


async def test_clerk_sees_everyone(cfg, monkeypatch, tmp_path):
    reg, _ = registry(cfg, monkeypatch, tmp_path, "clerk")
    r = await lookup(reg, first_name="Aarav", last_name="Deshmukh")
    assert r["status"] == "multiple_matches"
    r = await lookup(reg, first_name="Priya", last_name="Kulkarni")
    assert r["status"] == "found"


def test_cli_store_roles_and_classes(tmp_path):
    store = StaffStore(tmp_path / "staff.json", "secret")
    entry, _ = store.add("Meena Kale", role="teacher", classes=["8-A"])
    store.set_classes(entry["id"], ["8-A", "9-C"])
    store.set_role(entry["id"], "clerk")
    again = StaffStore(tmp_path / "staff.json", "secret").get(entry["id"])
    assert again["classes"] == ["8-A", "9-C"] and again["role"] == "clerk"


def test_pins_must_be_six_digits(tmp_path):
    store = StaffStore(tmp_path / "staff.json", "secret")
    with pytest.raises(ValueError):
        store.add("Short PIN", pin="4321")
    _, pin = store.add("Random PIN")
    assert len(pin) == 6
