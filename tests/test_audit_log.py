"""Feature 5: audit log."""
import csv
import sqlite3

import httpx
import pytest

from voice_agent.features.audit_log import AuditLog, CallAudit
from voice_agent.features.staff_pins import StaffSession, StaffStore
from voice_agent.tools.registry import ToolRegistry

RECORDS = [
    {"id": 3, "first_name": "Priya", "last_name": "Kulkarni", "class": "10", "division": "B", "roll_number": 27,
     "attendance_percent": 97.2, "last_exam_result": "Distinction", "phone": "9800000003",
     "address": "Panchavati", "date_of_birth": "2010-01-25"},
]


@pytest.fixture
def audit(tmp_path):
    return AuditLog(tmp_path / "audit.db")


def test_chain_verifies_and_detects_deletion(audit, tmp_path):
    for i in range(4):
        audit.record(event="lookup", call_id="c1", staff_id="S01", status="found", students=[f"S{i}"])
    assert audit.verify()[0]
    db = sqlite3.connect(tmp_path / "audit.db")
    db.execute("DELETE FROM audit WHERE id = 2")
    db.commit()
    ok, msg = audit.verify()
    assert not ok and "row 3" in msg


def test_rows_are_append_only_and_edits_detected(audit, tmp_path):
    audit.record(event="lookup", status="found", students=["Priya Kulkarni (10-B)"])
    db = sqlite3.connect(tmp_path / "audit.db")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE audit SET students = 'someone else'")
    db.execute("DROP TRIGGER audit_no_update")        # a determined attacker
    db.execute("UPDATE audit SET students = 'someone else'")
    db.commit()
    ok, msg = audit.verify()
    assert not ok and "modified" in msg


def test_sensitive_keys_never_stored(audit):
    audit.record(event="lookup", query={"last_name": "Kulkarni", "pin": "482913", "phone": "98000"},
                 fields_returned=["class", "phone", "address"])
    row = audit.rows()[0]
    assert "482913" not in str(row) and "98000" not in str(row)
    assert row["fields_returned"] == "class"


def test_export_csv(audit, tmp_path):
    audit.record(event="verify_ok", staff_id="S01", staff_name="Sunita Patil", status="verified")
    out = tmp_path / "audit.csv"
    assert audit.export_csv(out) == 1
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert rows[0]["staff_name"] == "Sunita Patil" and rows[0]["ts_ist"]


def test_purge_keeps_chain_valid(audit, tmp_path):
    for _ in range(3):
        audit.record(event="lookup")
    db = sqlite3.connect(tmp_path / "audit.db")
    db.execute("DROP TRIGGER audit_no_update")
    db.execute("DELETE FROM audit WHERE id = 1")  # simulate a purge of the oldest row
    db.commit()
    assert audit.verify()[0]


def make_registry(cfg, monkeypatch, audit, tmp_path):
    monkeypatch.setenv("STUDENT_API_BASE_URL", "http://api.test")
    store = StaffStore(tmp_path / "staff.json", "secret")
    store.add("Sunita Patil", pin="482913")
    session = StaffSession(store)

    def handler(request):
        last = request.url.params.get("last_name", "").lower()
        return httpx.Response(200, json={"results": [r for r in RECORDS if r["last_name"].lower() == last]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    call_audit = CallAudit(audit, session)
    return ToolRegistry(cfg, session, http_client=client, audit=call_audit), call_audit


async def test_lookup_is_logged_with_staff_and_no_sensitive_values(cfg, monkeypatch, audit, tmp_path):
    reg, call_audit = make_registry(cfg, monkeypatch, audit, tmp_path)
    await reg.call("verify_caller", {"pin": "482913"})
    reg.set_llm_provider("lmstudio")
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name": "Kulkarni",
                                                "confirmed_by_user": True})
    assert r["status"] == "found" and "_audit" not in r
    rows = audit.rows()
    assert [x["event"] for x in rows] == ["verify_ok", "lookup"]
    lookup = rows[1]
    assert lookup["staff_id"] == "S01" and lookup["staff_name"] == "Sunita Patil"
    assert lookup["students"] == "Priya Kulkarni (10-B)"
    assert lookup["llm_provider"] == "lmstudio" and lookup["call_id"] == call_audit.call_id
    assert "attendance_percent" in lookup["fields_returned"]
    text = str(rows)
    for secret in ("9800000003", "Panchavati", "2010-01-25", "97.2", "482913"):
        assert secret not in text


async def test_failed_pin_is_logged(cfg, monkeypatch, audit, tmp_path):
    reg, _ = make_registry(cfg, monkeypatch, audit, tmp_path)
    await reg.call("verify_caller", {"pin": "000000"})
    row = audit.rows()[0]
    assert row["event"] == "verify_fail" and row["staff_id"] is None


async def test_fail_closed_when_audit_breaks(cfg, monkeypatch, audit, tmp_path):
    reg, call_audit = make_registry(cfg, monkeypatch, audit, tmp_path)
    await reg.call("verify_caller", {"pin": "482913"})
    audit.close()  # every write now fails
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name": "Kulkarni",
                                                "confirmed_by_user": True})
    assert r["status"] == "error" and "student" not in r


def test_retention_purges_old_rows(tmp_path):
    audit = AuditLog(tmp_path / "audit.db", retention_days=7)
    audit.record(event="lookup")
    db = sqlite3.connect(tmp_path / "audit.db")
    db.execute("DROP TRIGGER audit_no_update")
    db.execute("UPDATE audit SET ts_utc = '2020-01-01T00:00:00Z'")  # pretend it's old
    db.commit()
    audit._last_purge = None
    audit.record(event="lookup")          # the next write triggers the purge
    assert [r["event"] for r in audit.rows()] == ["lookup"] and len(audit.rows()) == 1


def test_default_retention_is_seven_days(cfg):
    assert cfg["audit"]["retention_days"] == 7
