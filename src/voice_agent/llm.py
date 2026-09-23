"""LLM providers (LM Studio / OpenAI-compatible, Anthropic) behind a fallback router.

Events yielded by providers and the router:
  ("text", str)                                   streamed text
  ("tool_call", {"id", "name", "arguments": dict}) a complete tool call
"""
import asyncio
import json
import logging

from .breaker import CircuitBreaker
from .config import env

log = logging.getLogger(__name__)

FALLBACK_MESSAGE = "Sorry, I'm having trouble thinking right now. Please try again in a moment."
MIDSTREAM_MESSAGE = " Sorry, I lost my connection for a moment."


class ProviderError(Exception):
    pass


class ToolParseError(ProviderError):
    pass


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ToolParseError(f"invalid tool arguments: {raw[:200]}") from e
    if not isinstance(value, dict):
        raise ToolParseError("tool arguments must be an object")
    return value


class ThinkFilter:
    """Removes <think>...</think> blocks from a token stream (Qwen3 and similar)."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf = ""
        self.inside = False

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while True:
            if self.inside:
                i = self.buf.find(self.CLOSE)
                if i < 0:
                    self.buf = self.buf[-len(self.CLOSE):]
                    return out
                self.buf = self.buf[i + len(self.CLOSE):].lstrip()
                self.inside = False
            else:
                i = self.buf.find(self.OPEN)
                if i >= 0:
                    out += self.buf[:i]
                    self.buf = self.buf[i + len(self.OPEN):]
                    self.inside = True
                    continue
                # Hold back a possible partial "<think" at the end.
                keep = 0
                for k in range(1, len(self.OPEN)):
                    if self.buf.endswith(self.OPEN[:k]):
                        keep = k
                out += self.buf[: len(self.buf) - keep]
                self.buf = self.buf[len(self.buf) - keep:]
                return out

    def flush(self) -> str:
        out = "" if self.inside else self.buf
        self.buf = ""
        return out


class OpenAICompatProvider:
    """LM Studio, Ollama, vLLM, Sarvam and any other OpenAI-compatible server."""

    def __init__(self, name, base_url, model, api_key=None, temperature=0.4, max_tokens=400,
                 supports_tools=True, first_token_timeout=None, client=None):
        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.supports_tools = supports_tools
        self.first_token_timeout = first_token_timeout
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=60, max_retries=0)
        self.client = client

    async def health(self):
        models = await self.client.models.list()
        ids = [m.id for m in models.data]
        if not ids:
            raise ProviderError(f"{self.name}: no models available")
        if self.model not in ids:
            log.warning("%s: model '%s' not in %s; the server may load it on demand", self.name, self.model, ids)
        # Warm-up so the first real turn is not slowed by model loading.
        await self.client.chat.completions.create(
            model=self.model, messages=[{"role": "user", "content": "hi"}], max_tokens=1)

    async def stream(self, messages, tools):
        kwargs = dict(model=self.model, messages=messages, temperature=self.temperature,
                      max_tokens=self.max_tokens, stream=True)
        if tools and self.supports_tools:
            kwargs["tools"] = tools
        elif not self.supports_tools:
            messages = _flatten_tool_messages(messages)
            kwargs["messages"] = messages
        stream = await self.client.chat.completions.create(**kwargs)
        think = ThinkFilter()
        calls: dict[int, dict] = {}
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                text = think.feed(delta.content)
                if text:
                    yield ("text", text)
            for tc in getattr(delta, "tool_calls", None) or []:
                c = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                if tc.id:
                    c["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        c["name"] += tc.function.name
                    if tc.function.arguments:
                        c["arguments"] += tc.function.arguments
        tail = think.flush()
        if tail:
            yield ("text", tail)
        for i, c in sorted(calls.items()):
            yield ("tool_call", {"id": c["id"] or f"call_{i}", "name": c["name"],
                                 "arguments": _parse_args(c["arguments"])})


def _flatten_tool_messages(messages):
    """For providers without tool support, drop tool plumbing but keep the facts."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "user", "content": f"(Tool result: {m['content']})"})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            if m.get("content"):
                out.append({"role": "assistant", "content": m["content"]})
        else:
            out.append(m)
    return out


def to_anthropic(messages):
    """Convert OpenAI-style messages to Anthropic (system, messages)."""
    system_parts, converted = [], []
    for m in messages:
        role = m["role"]
        if role == "system":
            system_parts.append(m["content"])
            continue
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            converted.append({"role": "user", "content": [block]})
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"],
                               "input": _parse_args(tc["function"]["arguments"])})
            converted.append({"role": "assistant", "content": blocks})
            continue
        content = m.get("content") or ""
        converted.append({"role": role, "content": [{"type": "text", "text": content}] if content else []})

    merged = []
    for m in converted:
        if not m["content"]:
            continue
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"].extend(m["content"])
        else:
            merged.append({"role": m["role"], "content": list(m["content"])})
    # Tool results must come before any text in a user turn.
    for m in merged:
        if m["role"] == "user":
            m["content"].sort(key=lambda b: 0 if b["type"] == "tool_result" else 1)
    return "\n\n".join(system_parts), merged


class AnthropicProvider:
    def __init__(self, name, model, api_key, temperature=0.4, max_tokens=400, first_token_timeout=None, client=None):
        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.first_token_timeout = first_token_timeout
        self.api_key = api_key
        if client is None and api_key:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=api_key, max_retries=0)
        self.client = client

    async def health(self):
        if not self.client:
            raise ProviderError(f"{self.name}: API key not set")

    async def stream(self, messages, tools):
        if not self.client:
            raise ProviderError(f"{self.name}: API key not set")
        system, msgs = to_anthropic(messages)
        kwargs = dict(model=self.model, max_tokens=self.max_tokens, system=system,
                      messages=msgs, temperature=self.temperature)
        if tools:
            kwargs["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"],
                                "input_schema": t["function"]["parameters"]} for t in tools]
        async with self.client.messages.stream(**kwargs) as s:
            async for event in s:
                if event.type == "text" and event.text:
                    yield ("text", event.text)
            final = await s.get_final_message()
        for block in final.content:
            if block.type == "tool_use":
                yield ("tool_call", {"id": block.id, "name": block.name, "arguments": dict(block.input)})


class LLMRouter:
    def __init__(self, providers, breaker: CircuitBreaker, first_token_timeout=2.5, total_timeout=20.0):
        self.providers = providers
        self.breaker = breaker
        self.first_token_timeout = first_token_timeout
        self.total_timeout = total_timeout
        self.last_provider = None

    async def health_check(self):
        for p in self.providers:
            try:
                await asyncio.wait_for(p.health(), timeout=120)
                log.info("LLM provider ready: %s (%s)", p.name, p.model)
            except Exception as e:  # noqa: BLE001
                log.warning("LLM provider unavailable at startup: %s -> %s", p.name, e)
                self.breaker.trip(p.name)

    async def generate(self, messages, tools):
        loop = asyncio.get_running_loop()
        for p in self.providers:
            if self.breaker.is_open(p.name):
                continue
            yielded = False
            agen = p.stream(messages, tools).__aiter__()
            try:
                deadline = loop.time() + self.total_timeout
                first_timeout = p.first_token_timeout or self.first_token_timeout
                while True:
                    timeout = first_timeout if not yielded else max(0.1, deadline - loop.time())
                    try:
                        event = await asyncio.wait_for(agen.__anext__(), timeout)
                    except StopAsyncIteration:
                        break
                    yielded = True
                    yield event
                self.breaker.success(p.name)
                self.last_provider = p.name
                return
            except (GeneratorExit, asyncio.CancelledError):
                raise
            except Exception as e:  # noqa: BLE001
                self.breaker.failure(p.name)
                log.warning("LLM provider %s failed (%s: %s)", p.name, type(e).__name__, e)
                if yielded:
                    yield ("text", MIDSTREAM_MESSAGE)
                    return
            finally:
                try:
                    await agen.aclose()
                except Exception:  # noqa: BLE001
                    pass
        yield ("text", FALLBACK_MESSAGE)


def build_llm_router(cfg: dict) -> LLMRouter:
    llm = cfg["llm"]
    providers = []
    local = llm["local"]
    providers.append(OpenAICompatProvider(
        name=local["provider"], base_url=local["base_url"], model=local["model"],
        api_key=env(local.get("api_key_env")), temperature=local.get("temperature", 0.4),
        max_tokens=local.get("max_tokens", 400), supports_tools=local.get("supports_tools", True),
        first_token_timeout=(local["first_token_timeout_ms"] / 1000) if local.get("first_token_timeout_ms") else None))
    for c in llm.get("cloud", []):
        key = env(c.get("api_key_env"))
        name = c.get("name", c["provider"])
        if c["provider"] == "anthropic":
            providers.append(AnthropicProvider(name, c["model"], key, c.get("temperature", 0.4), c.get("max_tokens", 400)))
        else:
            if not key:
                log.info("Skipping %s: %s not set", name, c.get("api_key_env"))
                continue
            providers.append(OpenAICompatProvider(
                name=name, base_url=c["base_url"], model=c["model"], api_key=key,
                temperature=c.get("temperature", 0.4), max_tokens=c.get("max_tokens", 400),
                supports_tools=c.get("supports_tools", True)))
    fb = llm["fallback"]
    breaker = CircuitBreaker(fb["circuit_breaker"]["failure_threshold"], fb["circuit_breaker"]["cooldown_seconds"])
    return LLMRouter(providers, breaker, fb["first_token_timeout_ms"] / 1000, fb["total_timeout_ms"] / 1000)
