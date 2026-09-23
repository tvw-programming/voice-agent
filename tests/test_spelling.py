"""Feature 3: spelling mode for names."""
import httpx
import pytest

from voice_agent.features.spelling import (asks_to_spell, assemble_spelling, letters_for_readback,
                                           spelling_audio_config)
from voice_agent.tools.registry import Session, ToolRegistry


@pytest.mark.parametrize("heard,name", [
    ("P R I Y A", "Priya"),
    ("P-R-I-Y-A", "Priya"),
    ("p. r. i. y. a.", "Priya"),
    ("pee are eye why ay", "Priya"),
    ("K U L K A R N I", "Kulkarni"),
    ("D E S H M U K H", "Deshmukh"),
    ("B for Bombay, H, A, double T", "Bhatt"),
    ("P jaise Pune, R, I, Y, A", "Priya"),
    ("S as in Sugar, N, E, H, A", "Sneha"),
    ("that is P R I Y A", "Priya"),
    ("PRIYA", "Priya"),
])
def test_assemble(heard, name):
    assert assemble_spelling(heard) == name


def test_unclear_spelling():
    assert assemble_spelling("") is None
    assert assemble_spelling("hello there") is None


def test_readback_and_prompt_detection():
    assert letters_for_readback("Priya") == "P-R-I-Y-A"
    assert asks_to_spell("Could you spell the surname for me?")
    assert asks_to_spell("कृपया स्पेलिंग बताइए")
    assert not asks_to_spell("Priya is in class ten B.")


def test_spelling_audio_profile_does_not_touch_original(cfg):
    audio = cfg["audio"]
    spell = spelling_audio_config(audio, {"min_silence_ms": 1500, "max_utterance_seconds": 30})
    assert spell["vad"]["min_silence_ms"] == 1500 and spell["max_utterance_seconds"] == 30
    assert audio["vad"]["min_silence_ms"] == 600


RECORDS = [
    {"first_name": "Priya", "last_name": "Kulkarni", "class": "10", "division": "B", "roll_number": 27},
    {"first_name": "Rohan", "last_name": "Patel", "class": "9", "division": "C", "roll_number": 18},
]


def registry(cfg, monkeypatch):
    monkeypatch.setenv("STUDENT_API_BASE_URL", "http://api.test")

    def handler(request):  # loose API: matches on the first 3 letters of the surname
        last = request.url.params.get("last_name", "").lower()
        return httpx.Response(200, json={"results": [r for r in RECORDS if last and r["last_name"].lower()[:3] == last[:3]]})

    session = Session("4321")
    session.authenticated = True
    return ToolRegistry(cfg, session, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_lookup_with_spelled_surname(cfg, monkeypatch):
    reg = registry(cfg, monkeypatch)
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name_spelled": "K U L K A R N I",
                                                "confirmed_by_user": True})
    assert r["status"] == "found" and r["student"]["full_name"] == "Priya Kulkarni"


async def test_readback_includes_letters(cfg, monkeypatch):
    reg = registry(cfg, monkeypatch)
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name_spelled": "K U L K A R N I",
                                                "confirmed_by_user": False})
    assert r["status"] == "needs_confirmation" and "K-U-L-K-A-R-N-I" in r["message"]


async def test_spelled_name_is_matched_strictly(cfg, monkeypatch):
    reg = registry(cfg, monkeypatch)
    # Spelled P-A-T-I-L must not silently match Patel.
    r = await reg.call("get_student_details", {"first_name": "Rohan", "last_name_spelled": "P A T I L",
                                                "confirmed_by_user": True})
    assert r["status"] == "not_found" and "suggest" not in r


async def test_unclear_letters(cfg, monkeypatch):
    reg = registry(cfg, monkeypatch)
    r = await reg.call("get_student_details", {"first_name": "Priya", "last_name_spelled": "umm",
                                                "confirmed_by_user": True})
    assert r["status"] == "spelling_unclear"
