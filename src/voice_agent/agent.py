"""Conversation agent: history, tool-calling loop, sentence-level streaming.

Feature hooks (all optional, so the agent works without them):
  filler       features/filler.FillerPicker   -> spoken while a slow tool runs
  interceptor  features/staff_pins.PinInterceptor -> PIN checked before the LLM sees it
"""
import json
import logging
import time

from .features.filler import FillerText, TurnMetrics, run_tools_with_filler
from .text import split_sentences

log = logging.getLogger(__name__)


class Agent:
    def __init__(self, cfg: dict, router, tools, system_prompt: str, filler=None, interceptor=None):
        self.router = router
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_history = cfg["agent"].get("max_history_messages", 24)
        self.max_rounds = cfg["agent"].get("max_tool_rounds", 4)
        self.history: list[dict] = []
        self.filler = filler
        self.interceptor = interceptor
        self.last_reply = ""
        self.metrics = TurnMetrics()

    def _messages(self) -> list[dict]:
        return [{"role": "system", "content": self.system_prompt}] + self.history

    def _trim(self):
        if len(self.history) <= self.max_history:
            return
        self.history = self.history[-self.max_history:]
        # Never start mid tool-exchange: drop until the first user message.
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)

    async def respond(self, user_text: str, metrics: TurnMetrics | None = None):
        """Async generator of speakable sentences. Filler lines are yielded as FillerText."""
        self.metrics = metrics or TurnMetrics()
        spoken_text = user_text
        if self.interceptor is not None:
            replaced = self.interceptor.intercept(user_text, self.last_reply)
            if replaced is not None:
                spoken_text = replaced  # the PIN itself never enters history or reaches the LLM
        self.history.append({"role": "user", "content": spoken_text})
        reply_parts: list[str] = []
        for _ in range(self.max_rounds):
            buf, full, calls = "", "", []
            async for kind, data in self.router.generate(self._messages(), self.tools.schemas()):
                if kind == "text":
                    buf += data
                    full += data
                    sentences, buf = split_sentences(buf)
                    for s in sentences:
                        yield s
                elif kind == "tool_call":
                    calls.append(data)
            if buf.strip():
                yield buf.strip()
            if full.strip():
                reply_parts.append(full.strip())

            if not calls:
                self.metrics.provider = getattr(self.router, "last_provider", None)
                self.history.append({"role": "assistant", "content": full.strip()})
                self.last_reply = " ".join(reply_parts)
                self._trim()
                return

            self.history.append({
                "role": "assistant",
                "content": full.strip() or None,
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                               for c in calls],
            })
            if hasattr(self.tools, "set_llm_provider"):
                self.tools.set_llm_provider(getattr(self.router, "last_provider", None))

            async def run_tools(calls=calls):
                results = []
                for c in calls:  # sequential: verify_caller must finish before a lookup
                    result = await self.tools.call(c["name"], c["arguments"])
                    log.info("tool %s -> %s", c["name"], result.get("status"))
                    results.append((c, result))
                return results

            results = []
            self.metrics.tools.extend(c["name"] for c in calls)
            tool_start = time.monotonic()
            async for kind, value in run_tools_with_filler(run_tools, self.filler, calls, user_text,
                                                           already_spoke=bool(full.strip())):
                if kind == "filler":
                    self.metrics.filler = value
                    yield FillerText(value)
                else:
                    results = value
            self.metrics.tool_ms += int((time.monotonic() - tool_start) * 1000)
            for c, result in results:
                self.history.append({"role": "tool", "tool_call_id": c["id"],
                                     "content": json.dumps(result, ensure_ascii=False)})
        yield "Sorry, I couldn't finish that request. Could you try asking again?"
        self.last_reply = " ".join(reply_parts)
        self._trim()
