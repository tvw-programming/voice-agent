"""Entry point.

  python -m voice_agent.main              # full voice mode (mic + speaker)
  python -m voice_agent.main --text       # type instead of speak (no audio needed)
  python -m voice_agent.main --gender male
"""
import argparse
import asyncio
import logging
import threading

from .agent import Agent
from .config import load_config, read_prompt
from .llm import build_llm_router
from .text import normalize_for_speech
from .tools import ToolRegistry
from .tools.registry import build_session

log = logging.getLogger("voice_agent")


def _is_exit(text: str, cfg: dict) -> bool:
    t = text.lower().strip(" .!?")
    return any(t == p or t.endswith(" " + p) for p in cfg["agent"].get("exit_phrases", []))


async def _build(cfg):
    router = build_llm_router(cfg)
    if cfg["llm"]["fallback"].get("health_check_on_start", True):
        await router.health_check()
    session = build_session(cfg)
    agent = Agent(cfg, router, ToolRegistry(cfg, session), read_prompt(cfg))
    return agent, router


async def run_text(cfg):
    agent, router = await _build(cfg)
    print(f"\n{cfg['agent']['name']}: {cfg['agent']['greeting']}   (type 'bye' to quit)\n")
    while True:
        user = (await asyncio.to_thread(input, "You: ")).strip()
        if not user:
            continue
        print(f"{cfg['agent']['name']}: ", end="", flush=True)
        async for sentence in agent.respond(user):
            print(sentence, end=" ", flush=True)
        print(f"   [{router.last_provider}]")
        if _is_exit(user, cfg):
            break


async def speak(agent_stream, tts, speaker, mic, vad, cfg) -> bool:
    """Synthesise sentences while playing earlier ones. Returns True if interrupted."""
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
        while (item := await audio_q.get()) is not None:
            if not interrupt.is_set():
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
    tts = TTSRouter(cfg["tts"], cfg["agent"], gender)
    log.info("Loading speech models (first run downloads them) ...")
    await asyncio.to_thread(stt.preload)
    await asyncio.to_thread(tts.preload)
    agent, router = await _build(cfg)
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
            audio = await asyncio.to_thread(listen_for_utterance, mic, vad, acfg, stop)
            if audio is None:
                break
            text = await stt.transcribe(audio, acfg["input_sample_rate"])
            if not text:
                continue
            if cfg["logging"].get("log_transcripts"):
                log.info("User: %s", text)
            interrupted = await speak(agent.respond(text), tts, speaker, mic, vad, cfg)
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
        asyncio.run(run_text(cfg) if args.text else run_voice(cfg, args.gender))
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    main()
