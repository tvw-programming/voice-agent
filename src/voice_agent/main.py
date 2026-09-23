"""Entry point.

  python -m voice_agent.main              # full voice mode (mic + speaker)
  python -m voice_agent.main --text       # type instead of speak (no audio needed)
  python -m voice_agent.main --gender male
"""
import argparse
import asyncio
import logging
import sys
import threading
import time

from .agent import Agent
from .config import load_config, read_prompt
from .features import spelling
from .features.audit_log import CallAudit, build_audit
from .features.filler import CachingTTS, FillerPicker, FillerText, TurnMetrics
from .features.staff_pins import PinInterceptor
from .llm import build_llm_router
from .text import normalize_for_speech
from .tools import ToolRegistry
from .tools.registry import build_session

log = logging.getLogger("voice_agent")


def _is_exit(text: str, cfg: dict) -> bool:
    t = text.lower().strip(" .!?")
    return any(t == p or t.endswith(" " + p) for p in cfg["agent"].get("exit_phrases", []))


def _gender(cfg, override=None):
    import os
    g = (override or os.environ.get("VOICE_GENDER") or cfg["tts"].get("preferred_gender", "female")).lower()
    return g if g in ("male", "female") else "female"


async def _build(cfg, gender):
    router = build_llm_router(cfg)
    if cfg["llm"]["fallback"].get("health_check_on_start", True):
        await router.health_check()
    session = build_session(cfg)                                    # Feature 6
    audit_log = build_audit(cfg)                                    # Feature 5
    audit = CallAudit(audit_log, session) if audit_log else None
    filler = FillerPicker(cfg.get("filler"), gender)                # Feature 1
    interceptor = PinInterceptor(session) if cfg.get("staff", {}).get("intercept_pin", True) else None
    agent = Agent(cfg, router, ToolRegistry(cfg, session, audit=audit), read_prompt(cfg),
                  filler=filler, interceptor=interceptor)
    if session.uses_staff_file:
        log.info("Staff PINs: %d active staff", len(session.store.active()))
    elif getattr(session, "_legacy_pin", None):
        log.warning("No staff enrolled; using the shared STAFF_PIN (legacy mode)")
    return agent, router


async def run_text(cfg, gender=None):
    agent, router = await _build(cfg, _gender(cfg, gender))
    print(f"\n{cfg['agent']['name']}: {cfg['agent']['greeting']}   (type 'bye' to quit)\n")
    while True:
        user = (await asyncio.to_thread(input, "You: ")).strip()
        if not user:
            continue
        print(f"{cfg['agent']['name']}: ", end="", flush=True)
        dim = sys.stdout.isatty()
        async for sentence in agent.respond(user):
            if isinstance(sentence, FillerText):
                sentence = f"\033[2m({sentence})\033[0m" if dim else f"({sentence})"
            print(sentence, end=" ", flush=True)
        m = agent.metrics
        extra = f", lookup {m.tool_ms} ms" if m.tools else ""
        print(f"   [{router.last_provider}{extra}]")
        if cfg["logging"].get("turn_metrics", True):
            log.debug(m.line())
        if _is_exit(user, cfg):
            break


async def speak(agent_stream, tts, speaker, mic, vad, cfg, on_first_audio=None) -> bool:
    """Synthesise sentences while playing earlier ones. Returns True if interrupted.

    `on_first_audio()` is called once, just before the first audio starts playing (turn metrics)."""
    from .audio_io import barge_in_monitor
    interrupt, done = threading.Event(), threading.Event()
    audio_q: asyncio.Queue = asyncio.Queue(maxsize=3)

    async def producer():
        try:
            async for sentence in agent_stream:
                if interrupt.is_set():
                    break
                spoken = normalize_for_speech(sentence)
                if not spoken:
                    continue
                if cfg["logging"].get("log_transcripts"):
                    log.info("Agent: %s", spoken)
                audio, sr = await tts.synthesize(spoken)
                if audio is not None:
                    await audio_q.put((audio, sr))
        finally:
            await agent_stream.aclose()
            await audio_q.put(None)

    async def consumer():
        first = True
        while (item := await audio_q.get()) is not None:
            if not interrupt.is_set():
                if first and on_first_audio is not None:
                    on_first_audio()
                first = False
                await asyncio.to_thread(speaker.play, item[0], item[1], interrupt)

    monitor = None
    if cfg["audio"]["barge_in"]["enabled"] and mic is not None:
        monitor = asyncio.create_task(asyncio.to_thread(barge_in_monitor, mic, vad, cfg["audio"], interrupt, done))
    await asyncio.gather(producer(), consumer())
    done.set()
    if monitor:
        await monitor
    return interrupt.is_set()


async def run_voice(cfg, gender):
    from .audio_io import VAD, Microphone, Speaker, listen_for_utterance
    from .stt import STTRouter
    from .tts import TTSRouter

    acfg = cfg["audio"]
    stt = STTRouter(cfg["stt"])
    tts = CachingTTS(TTSRouter(cfg["tts"], cfg["agent"], gender))
    log.info("Loading speech models (first run downloads them) ...")
    await asyncio.to_thread(stt.preload)
    await asyncio.to_thread(tts.preload)
    agent, router = await _build(cfg, tts.gender)
    if agent.filler is not None and agent.filler.enabled:
        await tts.warm([normalize_for_speech(p) for p in agent.filler.all_phrases()])   # Feature 1
    spell_cfg = cfg.get("spelling", {})
    whisper_cfg = stt.providers[0].cfg
    normal_prompt = whisper_cfg.get("initial_prompt")
    vad, speaker = VAD(acfg["input_sample_rate"]), Speaker()
    mic = Microphone(acfg["input_sample_rate"], acfg["block_size"])
    mic.start()
    stop = threading.Event()

    async def say(text):
        async def one():
            yield text
        return await speak(one(), tts, speaker, mic, vad, cfg)

    try:
        await say(cfg["agent"]["greeting"])
        log.info("Listening ... (Ctrl+C to quit)")
        while True:
            # Feature 3: after the agent asks the caller to spell, allow longer pauses between letters.
            spelling_turn = spell_cfg.get("enabled", True) and spelling.asks_to_spell(agent.last_reply)
            listen_cfg = spelling.spelling_audio_config(acfg, spell_cfg) if spelling_turn else acfg
            whisper_cfg["initial_prompt"] = spelling.SPELLING_STT_PROMPT if spelling_turn else normal_prompt
            audio = await asyncio.to_thread(listen_for_utterance, mic, vad, listen_cfg, stop)
            if audio is None:
                break
            metrics = TurnMetrics()                         # clock starts when the caller stops speaking
            text = await stt.transcribe(audio, acfg["input_sample_rate"])
            metrics.stt_ms = int((time.monotonic() - metrics.started) * 1000)
            if not text:
                continue
            if cfg["logging"].get("log_transcripts"):
                # Never log what an unverified caller says: it may be their PIN.
                log.info("User: %s", text if agent.tools.session.authenticated else "[hidden until verified]")
            interrupted = await speak(agent.respond(text, metrics), tts, speaker, mic, vad, cfg,
                                      on_first_audio=metrics.first_audio)
            if cfg["logging"].get("turn_metrics", True):
                log.info(metrics.line())
            if not interrupted:
                mic.flush()
            if _is_exit(text, cfg):
                break
    finally:
        stop.set()
        mic.stop()


def main():
    ap = argparse.ArgumentParser(description="Vani voice agent")
    ap.add_argument("--text", action="store_true", help="type instead of speaking")
    ap.add_argument("--gender", choices=["male", "female"], help="override preferred voice")
    ap.add_argument("--config", help="path to config.json")
    args = ap.parse_args()
    cfg = load_config(args.config)
    logging.basicConfig(level=cfg["logging"].get("level", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        asyncio.run(run_text(cfg, args.gender) if args.text else run_voice(cfg, args.gender))
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    main()
