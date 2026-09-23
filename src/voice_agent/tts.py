"""Text-to-speech with Indian voices: Indic Parler-TTS / Kokoro locally, Sarvam Bulbul in the cloud."""
import asyncio
import base64
import io
import logging
import os
import re

import numpy as np

from .breaker import CircuitBreaker
from .config import env

log = logging.getLogger(__name__)
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


class KokoroTTS:
    def __init__(self, name, cfg, gender):
        self.name, self.cfg = name, cfg
        self.voice = cfg[gender]["voice_id"]
        self._pipe = None

    def load(self):
        if self._pipe is None:
            from kokoro import KPipeline
            self._pipe = KPipeline(lang_code=self.cfg.get("lang_code", "h"))
        return self._pipe

    def _synth(self, text):
        pipe = self.load()
        chunks = [np.asarray(audio, dtype=np.float32) for _, _, audio in
                  pipe(text, voice=self.voice, speed=self.cfg.get("speed", 1.0)) if audio is not None]
        if not chunks:
            raise RuntimeError("Kokoro produced no audio")
        return np.concatenate(chunks), 24000

    async def synthesize(self, text, language):
        return await asyncio.to_thread(self._synth, text)


class IndicParlerTTS:
    def __init__(self, name, cfg, gender):
        self.name, self.cfg = name, cfg
        self.description = cfg[gender]["description"]
        self._bundle = None

    def load(self):
        if self._bundle is None:
            import torch
            from parler_tts import ParlerTTSForConditionalGeneration
            from transformers import AutoTokenizer
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            model = ParlerTTSForConditionalGeneration.from_pretrained(self.cfg["model"]).to(device)
            tok = AutoTokenizer.from_pretrained(self.cfg["model"])
            desc_tok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
            desc = desc_tok(self.description, return_tensors="pt").to(device)
            self._bundle = (model, tok, desc, device)
        return self._bundle

    def _synth(self, text):
        model, tok, desc, device = self.load()
        prompt = tok(text, return_tensors="pt").to(device)
        out = model.generate(input_ids=desc.input_ids, attention_mask=desc.attention_mask,
                             prompt_input_ids=prompt.input_ids, prompt_attention_mask=prompt.attention_mask)
        return out.cpu().numpy().squeeze().astype(np.float32), model.config.sampling_rate

    async def synthesize(self, text, language):
        return await asyncio.to_thread(self._synth, text)


class SarvamTTS:
    def __init__(self, name, cfg, gender):
        self.name, self.cfg = name, cfg
        self.voice = cfg[gender]["voice_id"]
        self.key = env(cfg.get("api_key_env"))

    def load(self):
        if not self.key:
            raise RuntimeError("SARVAM_API_KEY not set")

    async def synthesize(self, text, language):
        self.load()
        import httpx
        import soundfile as sf
        body = {"text": text, "target_language_code": language, "speaker": self.voice,
                "model": self.cfg.get("model", "bulbul:v3"), "pace": self.cfg.get("pace", 1.0),
                "speech_sample_rate": self.cfg.get("sample_rate", 24000)}
        if "v3" in body["model"]:
            body["temperature"] = self.cfg.get("temperature", 0.6)
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(SARVAM_TTS_URL, json=body, headers={"api-subscription-key": self.key})
            r.raise_for_status()
            wav = base64.b64decode("".join(r.json()["audios"]))
        audio, sr = sf.read(io.BytesIO(wav), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio, sr


_PROVIDERS = {"kokoro": KokoroTTS, "indic-parler-tts": IndicParlerTTS, "sarvam": SarvamTTS}


class TTSRouter:
    def __init__(self, cfg: dict, agent_cfg: dict, gender: str | None = None):
        self.gender = (gender or os.environ.get("VOICE_GENDER") or cfg.get("preferred_gender", "female")).lower()
        if self.gender not in ("male", "female"):
            raise ValueError("preferred_gender must be 'male' or 'female'")
        self.default_lang = agent_cfg.get("default_language", "en-IN")
        self.deva_lang = agent_cfg.get("devanagari_language", "hi-IN")
        self.providers = []
        for key in cfg["order"]:
            vcfg = cfg["voices"][key]
            self.providers.append(_PROVIDERS[vcfg["provider"]](key, vcfg, self.gender))
        self.timeout = cfg.get("timeout_ms", 15000) / 1000
        self.breaker = CircuitBreaker(3, 120)

    def preload(self):
        """Load local models now; any that fail are skipped for this session."""
        for p in self.providers:
            try:
                p.load()
                log.info("TTS ready: %s (%s voice)", p.name, self.gender)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("TTS %s unavailable: %s", p.name, e)
                self.breaker.trip(p.name)

    def language_for(self, text: str) -> str:
        return self.deva_lang if _DEVANAGARI.search(text) else self.default_lang

    async def synthesize(self, text: str):
        lang = self.language_for(text)
        for p in self.providers:
            if self.breaker.is_open(p.name):
                continue
            try:
                audio, sr = await asyncio.wait_for(p.synthesize(text, lang), self.timeout)
                self.breaker.success(p.name)
                return audio, sr
            except Exception as e:  # noqa: BLE001
                self.breaker.failure(p.name)
                log.warning("TTS %s failed: %s", p.name, e)
        return None, None
