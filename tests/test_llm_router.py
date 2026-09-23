import asyncio

from voice_agent.breaker import CircuitBreaker
from voice_agent.llm import FALLBACK_MESSAGE, LLMRouter, ThinkFilter, to_anthropic


class Fake:
    def __init__(self, name, events=(), delay=0.0, fail_after=None):
        self.name, self.model, self.events, self.delay, self.fail_after = name, name, events, delay, fail_after
        self.first_token_timeout = None
        self.calls = 0

    async def health(self):
        pass

    async def stream(self, messages, tools):
        self.calls += 1
        for i, e in enumerate(self.events):
            if self.fail_after is not None and i == self.fail_after:
                raise ConnectionError("dropped")
            await asyncio.sleep(self.delay)
            yield e


async def collect(router):
    return [e async for e in router.generate([], [])]


async def test_uses_local_when_healthy():
    local, cloud = Fake("local", [("text", "hi")]), Fake("cloud", [("text", "cloud")])
    r = LLMRouter([local, cloud], CircuitBreaker(3, 60), first_token_timeout=1)
    assert await collect(r) == [("text", "hi")] and cloud.calls == 0


async def test_falls_back_on_first_token_timeout():
    local, cloud = Fake("local", [("text", "slow")], delay=0.5), Fake("cloud", [("text", "cloud")])
    r = LLMRouter([local, cloud], CircuitBreaker(3, 60), first_token_timeout=0.1)
    assert await collect(r) == [("text", "cloud")]


async def test_breaker_skips_local_after_repeated_failures():
    local = Fake("local", [("text", "x")], fail_after=0)
    cloud = Fake("cloud", [("text", "cloud")])
    r = LLMRouter([local, cloud], CircuitBreaker(2, 60), first_token_timeout=1)
    for _ in range(3):
        await collect(r)
    assert local.calls == 2 and cloud.calls == 3


async def test_midstream_failure_does_not_duplicate():
    local = Fake("local", [("text", "Hello"), ("text", " world")], fail_after=1)
    cloud = Fake("cloud", [("text", "cloud")])
    r = LLMRouter([local, cloud], CircuitBreaker(3, 60), first_token_timeout=1)
    out = await collect(r)
    assert out[0] == ("text", "Hello") and cloud.calls == 0 and len(out) == 2


async def test_all_fail_gives_spoken_apology():
    r = LLMRouter([Fake("a", [("text", "x")], fail_after=0)], CircuitBreaker(3, 60), first_token_timeout=1)
    assert await collect(r) == [("text", FALLBACK_MESSAGE)]


def test_think_filter_streaming():
    f = ThinkFilter()
    out = "".join(f.feed(t) for t in ["<thi", "nk>reason", "ing</th", "ink>Hello", " <", "b>"]) + f.flush()
    assert out == "Hello <b>"


def test_to_anthropic_conversion():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Priya Kulkarni please"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "verify_caller", "arguments": '{"pin": "1"}'}},
            {"id": "t2", "type": "function", "function": {"name": "get_student_details", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "{}"},
        {"role": "tool", "tool_call_id": "t2", "content": "{}"},
    ]
    system, out = to_anthropic(msgs)
    assert system == "sys"
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["type"] for b in out[2]["content"]] == ["tool_result", "tool_result"]
    assert out[1]["content"][0]["input"] == {"pin": "1"}
