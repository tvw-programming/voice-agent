"""Conversation agent: history, tool-calling loop, sentence-level streaming."""
import json
import logging

from .text import split_sentences

log = logging.getLogger(__name__)


class Agent:
    def __init__(self, cfg: dict, router, tools, system_prompt: str):
        self.router = router
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_history = cfg["agent"].get("max_history_messages", 24)
        self.max_rounds = cfg["agent"].get("max_tool_rounds", 4)
        self.history: list[dict] = []

    def _messages(self) -> list[dict]:
        return [{"role": "system", "content": self.system_prompt}] + self.history

    def _trim(self):
        if len(self.history) <= self.max_history:
            return
        self.history = self.history[-self.max_history:]
        # Never start mid tool-exchange: drop until the first user message.
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)

    async def respond(self, user_text: str):
        """Async generator of speakable sentences."""
        self.history.append({"role": "user", "content": user_text})
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

            if not calls:
                self.history.append({"role": "assistant", "content": full.strip()})
                self._trim()
                return

            self.history.append({
                "role": "assistant",
                "content": full.strip() or None,
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                               for c in calls],
            })
            for c in calls:
                result = await self.tools.call(c["name"], c["arguments"])
                log.info("tool %s -> %s", c["name"], result.get("status"))
                self.history.append({"role": "tool", "tool_call_id": c["id"],
                                     "content": json.dumps(result, ensure_ascii=False)})
        yield "Sorry, I couldn't finish that request. Could you try asking again?"
        self._trim()
