"""Feature 1: filler line while a slow tool runs."""
import asyncio
import random

from voice_agent.agent import Agent
from voice_agent.features.filler import CachingTTS, FillerPicker, detect_language, run_tools_with_filler


def picker(delay_ms=20, gender="female"):
    return FillerPicker({"enabled": True, "delay_ms": delay_ms, "tools": ["get_student_details"]},
                        gender, rng=random.Random(1))


def test_detect_language():
    assert detect_language("Tell me about Priya Kulkarni") == "en"
    assert detect_language("mujhe Priya ka result bata do") == "hinglish"
    assert detect_language("प्रिया का रिजल्ट क्या है") == "hi"
    assert detect_language("प्रियाचा निकाल काय आहे") == "mr"


def test_gender_grammar():
    assert "rahi" in picker(gender="female").pick("mujhe Priya ka result bata do")
    assert "raha" in picker(gender="male").pick("mujhe Priya ka result bata do")
    assert picker(gender="male").pick("प्रियाचा निकाल काय आहे") == "एक मिनिट, बघतो."


def test_no_back_to_back_repeat():
    p = picker()
    picks = [p.pick("hello there") for _ in range(10)]
    assert all(a != b for a, b in zip(picks, picks[1:]))


async def _collect(gen):
    return [item async for item in gen]


async def test_slow_tool_gets_filler():
    async def slow():
        await asyncio.sleep(0.1)
        return ["done"]
    out = await _collect(run_tools_with_filler(slow, picker(), [{"name": "get_student_details"}], "hi", False))
    assert out[0][0] == "filler" and out[-1] == ("results", ["done"])


async def test_fast_tool_no_filler():
    async def fast():
        return ["done"]
    out = await _collect(run_tools_with_filler(fast, picker(delay_ms=200), [{"name": "get_student_details"}],
                                               "hi", False))
    assert out == [("results", ["done"])]


async def test_no_filler_for_other_tools_or_when_llm_already_spoke():
    async def slow():
        await asyncio.sleep(0.1)
        return []
    out = await _collect(run_tools_with_filler(slow, picker(), [{"name": "verify_caller"}], "hi", False))
    assert [k for k, _ in out] == ["results"]
    out = await _collect(run_tools_with_filler(slow, picker(), [{"name": "get_student_details"}], "hi", True))
    assert [k for k, _ in out] == ["results"]


class LookupRouter:
    def __init__(self):
        self.rounds = 0
        self.last_provider = "fake"

    async def generate(self, messages, tools):
        self.rounds += 1
        if self.rounds == 1:
            yield ("tool_call", {"id": "a", "name": "get_student_details",
                                 "arguments": {"first_name": "Priya", "last_name": "Kulkarni", "confirmed_by_user": True}})
        else:
            yield ("text", "Priya is in class ten B.")


class SlowTools:
    def schemas(self):
        return []

    async def call(self, name, args):
        await asyncio.sleep(0.1)
        return {"status": "found"}


async def test_agent_speaks_filler_before_answer(cfg):
    agent = Agent(cfg, LookupRouter(), SlowTools(), "sys", filler=picker())
    out = [s async for s in agent.respond("Tell me about Priya Kulkarni")]
    assert len(out) == 2 and out[1] == "Priya is in class ten B."
    assert out[0] in ("One moment, let me check.", "Just a second, checking the records.")
    # The filler is spoken only; it is not stored as part of the conversation.
    assert all("moment" not in str(m.get("content")) for m in agent.history)


async def test_caching_tts_serves_warmed_phrases():
    class FakeTTS:
        gender = "female"
        calls = 0

        async def synthesize(self, text):
            FakeTTS.calls += 1
            return [0.0], 24000

    tts = CachingTTS(FakeTTS())
    await tts.warm(["One moment, let me check."])
    assert FakeTTS.calls == 1
    await tts.synthesize("One moment, let me check.")
    assert FakeTTS.calls == 1          # served from cache
    await tts.synthesize("Something else.")
    assert FakeTTS.calls == 2
    assert tts.gender == "female"      # attributes pass through
