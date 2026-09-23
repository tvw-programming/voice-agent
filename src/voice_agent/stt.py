"""Speech-to-text: local faster-whisper first, Sarvam cloud fallback."""
import asyncio
import io
import logging

import numpy as np

from .breaker import CircuitBreaker
from .config import env

log = logging.getLogger(__name__)
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"


class WhisperSTT:
    name = "faster-whisper"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._model = None

    def load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            log.info("Loading Whisper %s ...", self.cfg["model"])
            self._model = WhisperModel(self.cfg["model"], device=self.cfg.get("device", "auto"),
                                       compute_type=self.cfg.get("compute_type", "default"))
        return self._model

    def _transcribe(self, audio: np.ndarray) -> str:
        model = self.load()
        segments, _ = model.transcribe(audio, language=self.cfg.get("language") or None, beam_size=1,
                                       initial_prompt=self.cfg.get("initial_prompt"), vad_filter=False)
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        return await asyncio.to_thread(self._transcribe, audio)


class SarvamSTT:
    name = "sarvam-stt"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.key = env(cfg.get("api_key_env"))

    async def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if not self.key:
            raise RuntimeError("SARVAM_API_KEY not set")
        import httpx
        import soundfile as sf
        buf = io.BytesIO()
        sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                SARVAM_STT_URL, headers={"api-subscription-key": self.key},
                files={"file": ("audio.wav", buf.getvalue(), "audio/wav")},
                data={k: v for k, v in {"model": self.cfg["model"], "mode": self.cfg.get("mode"),
                                         "language_code": self.cfg.get("language_code", "unknown")}.items() if v})
            r.raise_for_status()
            return (r.json().get("transcript") or "").strip()


class STTRouter:
    def __init__(self, cfg: dict):
        self.providers = [WhisperSTT(cfg["local"]), SarvamSTT(cfg["cloud"])]
        self.timeout = cfg.get("timeout_ms", 8000) / 1000
        self.breaker = CircuitBreaker(3, 60)

    def preload(self):
        try:
            self.providers[0].load()
        except Exception as e:  # noqa: BLE001
            log.warning("Local Whisper unavailable (%s); will use cloud STT", e)
            self.breaker.trip(self.providers[0].name)

    async def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        for p in self.providers:
            if self.breaker.is_open(p.name):
                continue
            try:
                text = await asyncio.wait_for(p.transcribe(audio, sample_rate), self.timeout)
                self.breaker.success(p.name)
                return text
            except Exception as e:  # noqa: BLE001
                self.breaker.failure(p.name)
                log.warning("STT %s failed: %s", p.name, e)
        return ""
