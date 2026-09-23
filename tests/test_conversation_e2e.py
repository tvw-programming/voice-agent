"""End to end: main._build wiring + speak() + the Python mock API as a real HTTP server.

Only the LLM is scripted; staff PINs, audit log, filler, class search and the
student API are all real.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from voice_agent import main
from voice_agent.features.audit_log import AuditLog
from voice_agent.features.filler import FillerText, TurnMetrics
from voice_agent.features.staff_pins import StaffStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def mock_api():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--port", str(port),
                             "--app-dir", str(ROOT / "mock_api"), "--log-level", "warning"])
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(url + "/students/search", timeout=0.3)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=5)


class ScriptedRouter:
    """Stands in for LM Studio: replies depend on what the conversation looks like."""

    def __init__(self):
        self.last_provider = "lmstudio"
        self.seen = []

    async def health_check(self):
        pass

    async def generate(self, messages, tools):
        self.seen.append(json.dumps(messages))
        last = messages[-1]
        await asyncio.sleep(0.01)
        if last["role"] == "tool":
            result = json.loads(last["content"])
            s = result.get("student", {})
            yield ("text", f"{s.get('full_name', 'Nobody')} is in class {s.get('class')} {s.get('division')}.")
        elif "verified as" in last["content"]:
            yield ("text", "Thank you, Sunita ji. Which student?")
        elif "8 A" in last["content"]:
            yield ("tool_call", {"id": "t1", "name": "get_student_details",
                                 "arguments": {"first_name": "Aarav", "class_name": "8", "division": "A",
                                               "confirmed_by_user": True}})
        else:
            yield ("text", "Sure. Please tell me your staff PIN.")


class FakeTTS:
    gender = "female"

    async def synthesize(self, text):
        return [0.0], 24000


class FakeSpeaker:
    def __init__(self):
        self.played = 0

    def play(self, audio, sr, interrupt):
        self.played += 1


async def test_full_conversation(cfg, monkeypatch, tmp_path, mock_api):
    monkeypatch.setenv("STAFF_PIN_SECRET", "e2e-secret")
    monkeypatch.setenv("STUDENT_API_BASE_URL", mock_api)
    monkeypatch.setenv("STUDENT_API_KEY", "mock-key")
    cfg["staff"]["file"] = str(tmp_path / "staff.json")
    cfg["audit"]["file"] = str(tmp_path / "audit.db")
    cfg["filler"]["delay_ms"] = 0            # always speak the filler, so the test is deterministic
    cfg["llm"]["fallback"]["health_check_on_start"] = False
    StaffStore(tmp_path / "staff.json", "e2e-secret").add("Sunita Patil", role="clerk", pin="482913")
    router = ScriptedRouter()
    monkeypatch.setattr(main, "build_llm_router", lambda cfg: router)

    agent, _ = await main._build(cfg, "female")

    async def say(text, metrics=None):
        return [s async for s in agent.respond(text, metrics)]

    assert await say("I need a student's details") == ["Sure. Please tell me your staff PIN."]
    assert " ".join(await say("four eight two nine one three")) == "Thank you, Sunita ji. Which student?"

    # The lookup turn goes through speak(), like voice mode.
    speaker, metrics = FakeSpeaker(), TurnMetrics()
    interrupted = await main.speak(agent.respond("Aarav in 8 A", metrics), FakeTTS(), speaker, None, None, cfg,
                                   on_first_audio=metrics.first_audio)
    assert not interrupted and speaker.played == 2           # filler + answer
    assert metrics.filler and metrics.tools == ["get_student_details"]
    assert metrics.first_audio_ms is not None and "first_audio_ms" in metrics.line()

    # The answer used the real API, and the PIN never reached the LLM.
    assert agent.last_reply == "Aarav Deshmukh is in class 8 A."
    assert all("482913" not in m and "four eight two" not in m for m in router.seen)

    rows = AuditLog(tmp_path / "audit.db").rows()
    assert [(r["event"], r["staff_name"]) for r in rows] == [("verify_ok", "Sunita Patil"),
                                                            ("lookup", "Sunita Patil")]
    assert rows[1]["students"] == "Aarav Deshmukh (8-A)" and rows[1]["llm_provider"] == "lmstudio"


async def test_filler_is_marked_for_text_mode(cfg):
    from voice_agent.agent import Agent
    from voice_agent.features.filler import FillerPicker

    class Router:
        last_provider = "x"
        n = 0

        async def generate(self, messages, tools):
            Router.n += 1
            if Router.n == 1:
                yield ("tool_call", {"id": "a", "name": "get_student_details", "arguments": {}})
            else:
                yield ("text", "Done here.")

    class Tools:
        def schemas(self):
            return []

        async def call(self, name, args):
            await asyncio.sleep(0.05)
            return {"status": "found"}

    agent = Agent(cfg, Router(), Tools(), "sys", filler=FillerPicker({"delay_ms": 10}, "female"))
    out = [s async for s in agent.respond("hello")]
    assert isinstance(out[0], FillerText) and not isinstance(out[1], FillerText)
    assert agent.metrics.tool_ms >= 40
