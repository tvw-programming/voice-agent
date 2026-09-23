"""Feature 4: sound-based matching for Indian names."""
import httpx
import pytest

from voice_agent.features.phonetic import indian_phonetic_key, part_similarity, spelling_variants
from voice_agent.tools.registry import Session, ToolRegistry


@pytest.mark.parametrize("heard,record,key", [
    ("Desmukh", "Deshmukh", "desmuk"),
    ("Kulkarny", "Kulkarni", "kulkarni"),
    ("Pria", "Priya", "pria"),
    ("Arav", "Aarav", "arav"),
    ("Chaudhari", "Choudhary", None),
])
def test_same_sounding_names_share_a_key(heard, record, key):
    assert indian_phonetic_key(heard) == indian_phonetic_key(record)
    if key:
        assert indian_phonetic_key(record) == key


def test_different_names_stay_apart():
    assert indian_phonetic_key("Patel") != indian_phonetic_key("Patil")
    assert 0.85 < part_similarity("Patel", "Patil") < 0.97
    assert part_similarity("Joshi", "Kulkarni") < 0.6


def test_spelling_variants():
    assert spelling_variants("Desmukh")[0] == "Deshmukh"
    assert spelling_variants("Kulkarny")[0] == "Kulkarni"
    assert all(indian_phonetic_key(v) == "desmuk" for v in spelling_variants("Desmukh"))


RECORDS = [
    {"first_name": "Aarav", "last_name": "Deshmukh", "class": "8", "division": "A", "roll_number": 12,
     "attendance_percent": 94.5, "phone": "1"},
    {"first_name": "Priya", "last_name": "Kulkarni", "class": "10", "division": "B", "roll_number": 27,
     "attendance_percent": 97.2, "phone": "2"},
    {"first_name": "Rohan", "last_name": "Patel", "class": "9", "division": "C", "roll_number": 18, "phone": "3"},
]


def registry(cfg, monkeypatch, fuzzy_api=False):
    monkeypatch.setenv("STUDENT_API_BASE_URL", "http://api.test")
    calls = []

    def handler(request):
        first = request.url.params.get("first_name", "").lower()
        last = request.url.params.get("last_name", "").lower()
        calls.append(last)
        if fuzzy_api:  # an API that does its own loose matching on surname
            res = [r for r in RECORDS if r["last_name"].lower()[:3] == last[:3]]
        else:          # exact matching, like most real APIs
            res = [r for r in RECORDS if (not first or r["first_name"].lower() == first) and r["last_name"].lower() == last]
        return httpx.Response(200, json={"results": res})

    session = Session("4321")
    session.authenticated = True
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ToolRegistry(cfg, session, http_client=client), calls


async def test_misheard_surname_found_via_variant(cfg, monkeypatch):
    reg, calls = registry(cfg, monkeypatch)
    r = await reg.call("get_student_details", {"first_name": "Aarav", "last_name": "Desmukh", "confirmed_by_user": True})
    assert r["status"] == "found" and r["student"]["full_name"] == "Aarav Deshmukh"
    assert "deshmukh" in calls


async def test_kulkarny_matches_kulkarni(cfg, monkeypatch):
    reg, _ = registry(cfg, monkeypatch)
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name": "Kulkarny", "confirmed_by_user": True})
    assert r["status"] == "found" and r["student"]["full_name"] == "Priya Kulkarni"


async def test_close_but_different_name_needs_confirmation(cfg, monkeypatch):
    reg, _ = registry(cfg, monkeypatch, fuzzy_api=True)
    r = await reg.call("get_student_details", {"first_name": "Rohan", "last_name": "Patil", "confirmed_by_user": True})
    assert r["status"] == "confirm_match"
    assert r["candidate"]["full_name"] == "Rohan Patel"
    assert "attendance_percent" not in str(r) and "phone" not in str(r)
    r = await reg.call("get_student_details", {"first_name": "Rohan", "last_name": "Patel", "confirmed_by_user": True})
    assert r["status"] == "found"


async def test_not_found_suggests_spelling(cfg, monkeypatch):
    reg, _ = registry(cfg, monkeypatch)
    r = await reg.call("get_student_details", {"first_name": "Nobody", "last_name": "Here", "confirmed_by_user": True})
    assert r["status"] == "not_found" and r["suggest"] == "ask_to_spell"
