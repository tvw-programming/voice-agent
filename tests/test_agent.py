from voice_agent.agent import Agent


class ScriptedRouter:
    """Round 1: two tool calls. Round 2: spoken answer."""
    def __init__(self):
        self.rounds = 0
        self.last_provider = "fake"

    async def generate(self, messages, tools):
        self.rounds += 1
        if self.rounds == 1:
            yield ("tool_call", {"id": "a", "name": "verify_caller", "arguments": {"pin": "4321"}})
        else:
            assert messages[-1]["role"] == "tool"
            for tok in ["Priya is in class ten B. ", "Her attendance is ", "ninety seven percent."]:
                yield ("text", tok)


class FakeTools:
    def __init__(self):
        self.called = []

    def schemas(self):
        return []

    async def call(self, name, args):
        self.called.append(name)
        return {"status": "verified"}


async def test_tool_round_then_streamed_sentences(cfg):
    tools = FakeTools()
    agent = Agent(cfg, ScriptedRouter(), tools, "sys")
    out = [s async for s in agent.respond("PIN is 4321")]
    assert out == ["Priya is in class ten B.", "Her attendance is ninety seven percent."]
    assert tools.called == ["verify_caller"]
    assert [m["role"] for m in agent.history] == ["user", "assistant", "tool", "assistant"]


async def test_history_trim_never_starts_with_tool(cfg):
    cfg["agent"]["max_history_messages"] = 3
    agent = Agent(cfg, ScriptedRouter(), FakeTools(), "sys")
    agent.history = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": None, "tool_calls": []},
                     {"role": "tool", "tool_call_id": "a", "content": "{}"},
                     {"role": "assistant", "content": "a1"}, {"role": "user", "content": "u2"}]
    agent._trim()
    assert agent.history[0]["role"] == "user"
