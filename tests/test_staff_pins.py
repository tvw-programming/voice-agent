"""Feature 6: one PIN per staff member."""
import json

import pytest

from voice_agent.agent import Agent
from voice_agent.features.staff_pins import (GlobalLockout, PinInterceptor, StaffSession, StaffStore,
                                             spoken_digits)


@pytest.fixture
def store(tmp_path):
    return StaffStore(tmp_path / "staff.json", "test-secret")


def test_spoken_digits_hindi_and_devanagari():
    assert spoken_digits("four three two one") == "4321"
    assert spoken_digits("char teen do ek") == "4321"
    assert spoken_digits("चार तीन दो एक") == "4321"
    assert spoken_digits("४३२१") == "4321"


def test_add_stores_hash_not_pin(store, tmp_path):
    entry, pin = store.add("Sunita Patil", "clerk")
    assert len(pin) == 6 and pin.isdigit()
    raw = (tmp_path / "staff.json").read_text()
    assert pin not in raw and entry["pin_hash"] in raw
    assert json.loads(raw)["staff"][0]["name"] == "Sunita Patil"


def test_pins_are_unique(store):
    store.add("A", pin="482913")
    with pytest.raises(ValueError):
        store.add("B", pin="482913")


def test_pin_identifies_staff(store):
    store.add("Sunita Patil", pin="482913")
    store.add("Ramesh Pawar", pin="175302")
    s = StaffSession(store)
    r = s.verify("one seven five three zero two")
    assert r == {"status": "verified", "staff_name": "Ramesh Pawar"}
    assert s.staff_id == "S02" and s.authenticated


def test_wrong_pins_lock_the_call(store):
    store.add("Sunita Patil", pin="482913")
    s = StaffSession(store)
    for left in (2, 1, 0):
        assert s.verify("111111") == {"status": "wrong_pin", "attempts_left": left}
    assert s.verify("482913")["status"] == "locked"


def test_disabled_staff_cannot_verify(store):
    entry, pin = store.add("Sunita Patil")
    store.set_active(entry["id"], False)
    s = StaffSession(store, legacy_pin="4321")
    # No active staff -> legacy shared PIN applies, the disabled PIN does not.
    assert s.verify(pin)["status"] == "wrong_pin"
    assert s.verify("4321")["status"] == "verified" and s.staff_id == "shared"


def test_reset_pin(store):
    entry, old = store.add("Sunita Patil")
    new = store.reset_pin(entry["id"])
    assert new != old
    assert StaffSession(store).verify(old)["status"] == "wrong_pin"
    assert StaffSession(store).verify(new)["status"] == "verified"


def test_wrong_secret_cannot_use_staff_file(store, tmp_path):
    store.add("Sunita Patil", pin="482913")
    other = StaffStore(tmp_path / "staff.json", "different-secret")
    assert StaffSession(other).verify("482913")["status"] == "wrong_pin"


def test_global_lockout_across_calls(store):
    store.add("Sunita Patil", pin="482913")
    now = [0.0]
    lock = GlobalLockout(max_failures=4, window_seconds=600, lock_seconds=900, clock=lambda: now[0])
    for _ in range(2):  # two calls, two wrong PINs each
        s = StaffSession(store, lockout=lock)
        s.verify("000001")
        s.verify("000002")
    assert StaffSession(store, lockout=lock).verify("482913")["status"] == "locked"
    now[0] += 901
    assert StaffSession(store, lockout=lock).verify("482913")["status"] == "verified"


def test_interceptor_only_when_asked(store):
    store.add("Sunita Patil", pin="482913")
    s = StaffSession(store)
    i = PinInterceptor(s)
    assert i.intercept("roll 12 of class 10", "Which student would you like?") is None
    out = i.intercept("four eight two nine one three", "Sure. Please tell me your staff PIN.")
    assert "verified as Sunita Patil" in out and "482913" not in out
    assert i.intercept("482913", "PIN please") is None  # already verified -> pass through


class RecordingRouter:
    def __init__(self):
        self.seen = []
        self.last_provider = "cloud"

    async def generate(self, messages, tools):
        self.seen.append(json.dumps(messages))
        yield ("text", "Please tell me your staff PIN." if len(self.seen) == 1 else "Thank you, Sunita ji.")


class NoTools:
    def schemas(self):
        return []


async def test_pin_never_reaches_llm_or_history(cfg, store):
    store.add("Sunita Patil", pin="482913")
    session = StaffSession(store)
    router = RecordingRouter()
    agent = Agent(cfg, router, NoTools(), "sys", interceptor=PinInterceptor(session))
    [s async for s in agent.respond("I need a student's details")]
    [s async for s in agent.respond("my PIN is four eight two nine one three")]
    assert session.authenticated and session.staff_name == "Sunita Patil"
    for dump in router.seen + [json.dumps(agent.history)]:
        assert "482913" not in dump and "four eight two" not in dump
