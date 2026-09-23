import httpx
import pytest

from voice_agent.tools.registry import Session, ToolRegistry

RECORDS = [
    {"first_name": "Aarav", "last_name": "Deshmukh", "class": "8", "division": "A", "roll_number": 12,
     "attendance_percent": 94.5, "last_exam_result": "First class", "phone": "98000", "address": "Nashik"},
    {"first_name": "Aarav", "last_name": "Deshmukh", "class": "6", "division": "C", "roll_number": 4,
     "attendance_percent": 88.0, "last_exam_result": "Distinction", "phone": "98001"},
    {"first_name": "Priya", "last_name": "Kulkarni", "class": "10", "division": "B", "roll_number": 27,
     "attendance_percent": 97.2, "last_exam_result": "Distinction", "phone": "98002",
     "date_of_birth": "2010-01-25"},
]


def make(cfg, monkeypatch, handler=None, pin="4321"):
    monkeypatch.setenv("STUDENT_API_BASE_URL", "http://api.test")
    monkeypatch.setenv("STUDENT_API_KEY", "k")
    calls = {"n": 0}

    def default(request):
        calls["n"] += 1
        first = request.url.params.get("first_name", "").lower()
        last = request.url.params.get("last_name", "").lower()
        res = [r for r in RECORDS if (not first or r["first_name"].lower() == first) and r["last_name"].lower() == last]
        return httpx.Response(200, json={"results": res})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler or default))
    reg = ToolRegistry(cfg, Session(pin), http_client=client)
    return reg, calls


async def verified(reg):
    assert (await reg.call("verify_caller", {"pin": "four three two one"}))["status"] == "verified"


async def test_requires_verification(cfg, monkeypatch):
    reg, _ = make(cfg, monkeypatch)
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name": "Kulkarni",
                                                "name_confirmed_by_user": True})
    assert r["status"] == "caller_not_verified"


async def test_wrong_pin_locks(cfg, monkeypatch):
    reg, _ = make(cfg, monkeypatch)
    for _ in range(3):
        assert (await reg.call("verify_caller", {"pin": "1111"}))["status"] == "wrong_pin"
    assert (await reg.call("verify_caller", {"pin": "4321"}))["status"] == "locked"


async def test_requires_confirmation(cfg, monkeypatch):
    reg, calls = make(cfg, monkeypatch)
    await verified(reg)
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name": "Kulkarni",
                                                "name_confirmed_by_user": False})
    assert r["status"] == "needs_confirmation" and calls["n"] == 0


async def test_found_and_sensitive_fields_removed(cfg, monkeypatch):
    reg, _ = make(cfg, monkeypatch)
    await verified(reg)
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name": "Kulkarni",
                                                "name_confirmed_by_user": True})
    assert r["status"] == "found"
    s = r["student"]
    assert s["full_name"] == "Priya Kulkarni" and s["class"] == "10"
    for bad in ("phone", "address", "date_of_birth"):
        assert bad not in s


async def test_fuzzy_match_via_last_name_fallback(cfg, monkeypatch):
    reg, _ = make(cfg, monkeypatch)
    await verified(reg)
    # STT heard "Pria" -> exact search fails, surname search + fuzzy match finds Priya.
    r = await reg.call("get_student_details", {"first_name": "Pria", "last_name": "Kulkarni",
                                                "name_confirmed_by_user": True})
    assert r["status"] == "found" and r["student"]["full_name"] == "Priya Kulkarni"


async def test_multiple_matches(cfg, monkeypatch):
    reg, _ = make(cfg, monkeypatch)
    await verified(reg)
    r = await reg.call("get_student_details", {"first_name": "Aarav", "last_name": "Deshmukh",
                                                "name_confirmed_by_user": True})
    assert r["status"] == "multiple_matches"
    assert {(o["class"], o["division"]) for o in r["options"]} == {("8", "A"), ("6", "C")}
    assert all("phone" not in s for s in r["students"])


async def test_not_found_and_cached(cfg, monkeypatch):
    reg, calls = make(cfg, monkeypatch)
    await verified(reg)
    args = {"first_name": "Nobody", "last_name": "Here", "name_confirmed_by_user": True}
    assert (await reg.call("get_student_details", args))["status"] == "not_found"
    n = calls["n"]
    await reg.call("get_student_details", args)
    assert calls["n"] == n


async def test_api_down_gives_friendly_error(cfg, monkeypatch):
    def boom(request):
        raise httpx.ConnectError("down")
    reg, _ = make(cfg, monkeypatch, handler=boom)
    await verified(reg)
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name": "Kulkarni",
                                                "name_confirmed_by_user": True})
    assert r["status"] == "error"
