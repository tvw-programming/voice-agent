"""Microphone capture, Silero VAD utterance detection, playback with barge-in."""
import collections
import logging
import queue
import threading
import time

import numpy as np

log = logging.getLogger(__name__)


class Microphone:
    def __init__(self, sample_rate=16000, block_size=512):
        import sounddevice as sd
        self.sample_rate, self.block_size = sample_rate, block_size
        self.q: queue.Queue = queue.Queue()
        self.pushback: collections.deque = collections.deque()
        self.stream = sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32",
                                     blocksize=block_size, callback=self._callback)

    def _callback(self, indata, frames, t, status):
        self.q.put(indata[:, 0].copy())

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()

    def read(self, timeout=0.5):
        if self.pushback:
            return self.pushback.popleft()
        return self.q.get(timeout=timeout)

    def flush(self):
        while not self.q.empty():
            self.q.get_nowait()


class VAD:
    def __init__(self, sample_rate=16000):
        import torch
        from silero_vad import load_silero_vad
        self.torch = torch
        self.model = load_silero_vad()
        self.sample_rate = sample_rate

    def prob(self, frame: np.ndarray) -> float:
        return float(self.model(self.torch.from_numpy(frame), self.sample_rate).item())

    def reset(self):
        self.model.reset_states()


def listen_for_utterance(mic: Microphone, vad: VAD, cfg: dict, stop: threading.Event) -> np.ndarray | None:
    vcfg = cfg["vad"]
    frame_ms = 1000 * mic.block_size / mic.sample_rate
    need_speech = max(1, int(vcfg["min_speech_ms"] / frame_ms))
    need_silence = max(1, int(vcfg["min_silence_ms"] / frame_ms))
    max_frames = int(cfg["max_utterance_seconds"] * 1000 / frame_ms)
    pre_roll = collections.deque(maxlen=max(1, int(vcfg["pre_roll_ms"] / frame_ms)))
    vad.reset()
    frames, speech_run, silence_run, active = [], 0, 0, False
    while not stop.is_set():
        try:
            frame = mic.read()
        except queue.Empty:
            continue
        p = vad.prob(frame)
        if not active:
            pre_roll.append(frame)
            speech_run = speech_run + 1 if p >= vcfg["threshold"] else 0
            if speech_run >= need_speech:
                active, frames = True, list(pre_roll)
        else:
            frames.append(frame)
            silence_run = silence_run + 1 if p < vcfg["threshold"] else 0
            if silence_run >= need_silence or len(frames) >= max_frames:
                return np.concatenate(frames)
    return None


def barge_in_monitor(mic: Microphone, vad: VAD, cfg: dict, interrupt: threading.Event, done: threading.Event):
    """Runs while the agent speaks; sets `interrupt` when the user starts talking."""
    bcfg = cfg["barge_in"]
    frame_ms = 1000 * mic.block_size / mic.sample_rate
    need = max(1, int(bcfg["min_speech_ms"] / frame_ms))
    recent = collections.deque(maxlen=need + int(cfg["vad"]["pre_roll_ms"] / frame_ms))
    run = 0
    vad.reset()
    while not done.is_set() and not interrupt.is_set():
        try:
            frame = mic.q.get(timeout=0.1)
        except queue.Empty:
            continue
        recent.append(frame)
        run = run + 1 if vad.prob(frame) >= bcfg["threshold"] else 0
        if run >= need:
            interrupt.set()
            mic.pushback.extend(recent)  # keep the start of what the user said


class Speaker:
    def play(self, audio: np.ndarray, sample_rate: int, interrupt: threading.Event) -> bool:
        import sounddevice as sd
        sd.play(audio, sample_rate)
        duration = len(audio) / sample_rate
        start = time.monotonic()
        while time.monotonic() - start < duration + 0.1:
            if interrupt.is_set():
                sd.stop()
                return False
            time.sleep(0.02)
        sd.wait()
        return True
