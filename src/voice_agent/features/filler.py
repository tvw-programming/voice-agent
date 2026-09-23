"""Feature 1: filler line while a slow tool runs.

When the LLM asks for a slow tool (the student API), the caller would hear
1-3 seconds of silence. If the tool hasn't finished after `delay_ms`, the agent
speaks a short line such as "Ek second, check kar rahi hoon." in the caller's
language and with grammar that matches the voice's gender.

Used by:
  agent.py  -> run_tools_with_filler(...), FillerText, TurnMetrics
  main.py   -> CachingTTS wraps the TTS router so filler audio plays instantly;
               turn metrics are logged so latency work has numbers to start from
"""
import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Common Hinglish words written in Latin script.
_HINGLISH = re.compile(
    r"\b(hai|hain|kya|kaise|mujhe|mera|meri|bata|batao|bataiye|chahiye|nahi|haan|ji|"
    r"aap|ka|ki|ke|ko|se|mein|wala|wali|kripya|dijiye|karo|kijiye)\b", re.I)
# A few Marathi-only words, to tell Marathi from Hindi in Devanagari.
_MARATHI = re.compile(r"(आहे|आहेत|काय|मला|तुम्ही|सांगा|नाही|होय|च्या|ची|चा)")

DEFAULT_PHRASES = {
    "en": {
        "female": ["One moment, let me check.", "Just a second, checking the records."],
        "male": ["One moment, let me check.", "Just a second, checking the records."],
    },
    "hinglish": {
        "female": ["Ek second, check kar rahi hoon.", "Bas ek minute, dekh rahi hoon."],
        "male": ["Ek second, check kar raha hoon.", "Bas ek minute, dekh raha hoon."],
    },
    "hi": {
        "female": ["एक सेकंड, देख रही हूँ।"],
        "male": ["एक सेकंड, देख रहा हूँ।"],
    },
    "mr": {
        "female": ["एक मिनिट, बघते."],
        "male": ["एक मिनिट, बघतो."],
    },
}


class FillerText(str):
    """A sentence that is a filler line (spoken, but shown dimmed in text mode and never stored)."""


@dataclass
class TurnMetrics:
    """Timings for one caller turn. Logged as one line when logging.turn_metrics is true."""
    started: float = field(default_factory=time.monotonic)
    stt_ms: int | None = None
    tool_ms: int = 0
    tools: list[str] = field(default_factory=list)
    filler: str | None = None
    first_audio_ms: int | None = None
    provider: str | None = None

    def first_audio(self):
        if self.first_audio_ms is None:
            self.first_audio_ms = int((time.monotonic() - self.started) * 1000)

    def line(self) -> str:
        parts = []
        if self.stt_ms is not None:
            parts.append(f"stt_ms={self.stt_ms}")
        if self.first_audio_ms is not None:
            parts.append(f"first_audio_ms={self.first_audio_ms}")
        parts.append(f"tool_ms={self.tool_ms}")
        parts.append(f"tools={','.join(self.tools) or '-'}")
        parts.append(f"filler={'yes' if self.filler else 'no'}")
        parts.append(f"llm={self.provider or '-'}")
        return "turn " + " ".join(parts)


def detect_language(text: str) -> str:
    """Return 'en', 'hinglish', 'hi' or 'mr' for the caller's last message."""
    text = text or ""
    if _DEVANAGARI.search(text):
        return "mr" if _MARATHI.search(text) else "hi"
    if len(_HINGLISH.findall(text)) >= 2:
        return "hinglish"
    return "en"


class FillerPicker:
    """Chooses a filler phrase; never repeats the previous one back-to-back."""

    def __init__(self, cfg: dict | None, gender: str = "female", rng: random.Random | None = None):
        cfg = cfg or {}
        self.enabled = cfg.get("enabled", True)
        self.delay = cfg.get("delay_ms", 350) / 1000
        self.tools = set(cfg.get("tools", ["get_student_details"]))
        self.phrases = cfg.get("phrases") or DEFAULT_PHRASES
        self.gender = gender if gender in ("male", "female") else "female"
        self.rng = rng or random.Random()
        self._last: str | None = None

    def applies(self, calls: list[dict]) -> bool:
        return self.enabled and any(c.get("name") in self.tools for c in calls)

    def _options(self, lang: str) -> list[str]:
        entry = self.phrases.get(lang) or self.phrases.get("en") or {}
        if isinstance(entry, list):  # same phrases for both genders
            return entry
        return entry.get(self.gender) or entry.get("female") or []

    def pick(self, user_text: str) -> str | None:
        options = self._options(detect_language(user_text))
        if not options:
            return None
        choices = [p for p in options if p != self._last] or options
        self._last = self.rng.choice(choices)
        return self._last

    def all_phrases(self) -> list[str]:
        """Every phrase for the current gender, used to pre-synthesise audio."""
        out = []
        for lang in self.phrases:
            out.extend(self._options(lang))
        return out


async def run_tools_with_filler(run_tools, filler: "FillerPicker | None", calls: list[dict],
                                user_text: str, already_spoke: bool):
    """Run `run_tools()` (a coroutine function returning the tool results).

    Async generator: yields ("filler", text) at most once if the tools are slow,
    then ("results", list). The caller forwards the filler to TTS immediately,
    so the tool call and the filler audio overlap.
    """
    task = asyncio.create_task(run_tools())
    if filler is not None and not already_spoke and filler.applies(calls):
        done, _ = await asyncio.wait({task}, timeout=filler.delay)
        if not done:
            phrase = filler.pick(user_text)
            if phrase:
                log.debug("filler: %s", phrase)
                yield ("filler", phrase)
    yield ("results", await task)


class CachingTTS:
    """Wraps a TTS router and caches audio for fixed phrases (the fillers).

    Only phrases passed to `warm()` are cached, so normal replies are untouched.
    """

    def __init__(self, tts):
        self.tts = tts
        self._cache: dict[str, tuple] = {}

    def __getattr__(self, name):  # preload(), gender, language_for() ...
        return getattr(self.tts, name)

    async def warm(self, phrases: list[str]):
        for phrase in phrases:
            try:
                audio, sr = await self.tts.synthesize(phrase)
                if audio is not None:
                    self._cache[phrase] = (audio, sr)
            except Exception as e:  # noqa: BLE001
                log.warning("Could not pre-synthesise filler %r: %s", phrase, e)
        log.info("Filler audio cached: %d phrases", len(self._cache))

    async def synthesize(self, text: str):
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        return await self.tts.synthesize(text)
