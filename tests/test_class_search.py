"""Feature 2: search by class, division and roll number (runs against the mock API)."""
import importlib.util
from pathlib import Path

import httpx
import pytest

from voice_agent.features.class_search import (normalize_class, normalize_division, normalize_roll,
                                               search_shape)
from voice_agent.tools.registry import Session, ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


def test_normalize_class():
    for spoken in ["8", "8th", "eight", "eighth", "VIII", "aathvi", "class eight", "Std 8"]:
        assert normalize_class(spoken) == "8", spoken
    assert normalize_class("Jr KG") == "LKG" and normalize_class("sr. kg") == "UKG"
    assert normalize_class("11 science") == "11 Science"
    assert normalize_class("banana") is None and normalize_class("15") is None


def test_normalize_division_and_roll():
    assert [normalize_division(x) for x in ["a", "A", "ay", "division B", "8-A", "see"]] == ["A", "A", "A", "B", "A", "C"]
    assert normalize_division("Rose", ["Rose", "Lotus"]) == "Rose"
    assert [normalize_roll(x) for x in ["12", "twelve", "roll number forty two", "baara"]] == ["12", "12", "42", "12"]


def test_search_shapes():
    assert search_shape("Priya", "Kulkarni", None, None, None) == "name"
    assert search_shape("Aarav", "Deshmukh", "8", "A", None) == "name_class"
    assert search_shape("Aarav", "", "8", "A", None) == "first_class"
    assert search_shape("", "", "8", "A", "12") == "class_roll"
    assert search_shape("", "", "8", None, None) is None
    assert search_shape("", "", None, None, "12") is None


def load_mock_app():
    spec = importlib.util.spec_from_file_location("mock_server", ROOT / "mock_api" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


@pytest.fixture
def registry(cfg, monkeypatch):
    monkeypatch.setenv("STUDENT_API_BASE_URL", "http://mock")
    monkeypatch.setenv("STUDENT_API_KEY", "mock-key")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=load_mock_app()), base_url="http://mock")
    session = Session("4321")
    session.authenticated = True

    def make(mode="api"):
        cfg["tools"]["student_lookup"].setdefault("class_search", {})["mode"] = mode
        return ToolRegistry(cfg, session, http_client=client)
    return make


async def lookup(reg, **args):
    return await reg.call("get_student_details", {"confirmed_by_user": True, **args})


async def test_first_name_and_class(registry):
    r = await lookup(registry(), first_name="Aarav", class_name="eighth", division="A")
    assert r["status"] == "found" and r["student"]["full_name"] == "Aarav Deshmukh"
    assert r["student"]["class"] == "8"


async def test_class_division_roll(registry):
    r = await lookup(registry(), class_name="8", division="ay", roll_number="twelve")
    assert r["status"] == "found" and r["student"]["full_name"] == "Aarav Deshmukh"


async def test_full_name_plus_class_picks_one_of_duplicates(registry):
    reg = registry()
    r = await lookup(reg, first_name="Aarav", last_name="Deshmukh")
    assert r["status"] == "multiple_matches"
    r = await lookup(reg, first_name="Aarav", last_name="Deshmukh", class_name="6", division="C")
    assert r["status"] == "found" and r["student"]["roll_number"] == 4


async def test_not_enough_info(registry):
    r = await lookup(registry(), class_name="8")
    assert r["status"] == "need_more_info" and "division" in r["message"]
    r = await lookup(registry(), roll_number="12")
    assert r["status"] == "need_more_info"


async def test_roll_search_disabled_in_client_filter_mode(registry):
    r = await lookup(registry(mode="client_filter"), class_name="8", division="A", roll_number="12")
    assert r["status"] == "need_more_info"


async def test_confirmation_readback_mentions_class(cfg, monkeypatch, registry):
    reg = registry()
    r = await reg.call("get_student_details", {"class_name": "8", "division": "A", "roll_number": "12",
                                                "confirmed_by_user": False})
    assert r["status"] == "needs_confirmation" and "roll 12 of 8-A" in r["message"]


async def test_legacy_confirmation_flag_still_works(registry):
    r = await registry().call("get_student_details", {"first_name": "Priya", "last_name": "Kulkarni",
                                                       "name_confirmed_by_user": True})
    assert r["status"] == "found"
