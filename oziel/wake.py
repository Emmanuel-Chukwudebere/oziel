"""Wake word — "Oziel", learned from the owner's own voice, fully local.

No custom model to train: openWakeWord's pretrained speech-feature models
(Apache-2.0, 2.4 MB in oziel/models/) turn audio into 96-d embeddings, and
three takes of the owner saying "Oziel" become the templates — so it answers
to the owner's voice saying its name, not just the name. Matching the
word's shape over time — three windows in order, not one snapshot — is what
keeps near-rhymes like "Ezekiel" out.

It listens only while Oziel sleeps: the live socket is closed then, and no
audio leaves the laptop until the word is heard.
"""
import io
import threading
import time
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort
import sounddevice as sd

from oziel import config

MODELS = Path(__file__).parent / "models"
TEMPLATES = config.CONFIG_DIR / "wake.npy"
RATE = 16_000
HOP = 1_280              # 80ms per detector step = 8 mel frames
OVERLAP = 480            # extra samples so each hop's mel frames line up
WIN = 76                 # mel frames per embedding (~0.76s)
OFFSETS = (-10, 0, 10)   # the word's start, middle, end, in mel frames
# the templates are the owner's own takes, so the score carries who said it
# as well as what: SAPI test — enrolled voice 0.965-0.993, a different voice
# saying "Oziel" 0.930-0.939, closest rhyme ("Ezekiel") 0.888
THRESHOLD = 0.95
SPEECH_RMS = 0.012 * 32768
COOLDOWN = 2.0           # seconds deaf after a hit


class _Features:
    """melspectrogram + embedding models, loaded once, single-threaded so a
    sleeping Oziel never competes with the owner's work for cores."""
    _inst = None

    def __init__(self):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        cpu = ["CPUExecutionProvider"]
        self.mel = ort.InferenceSession(str(MODELS / "melspectrogram.onnx"), opts, providers=cpu)
        self.emb = ort.InferenceSession(str(MODELS / "embedding_model.onnx"), opts, providers=cpu)

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def melspec(self, pcm: np.ndarray) -> np.ndarray:
        out = self.mel.run(None, {"input": pcm.astype(np.float32)[None]})[0]
        return out.reshape(-1, 32) / 10 + 2

    def embed(self, windows: np.ndarray) -> np.ndarray:
        e = self.emb.run(None, {"input_1": windows[..., None].astype(np.float32)})[0]
        e = e.reshape(len(windows), 96)
        return e / np.linalg.norm(e, axis=1, keepdims=True)


def _rms(pcm: np.ndarray) -> float:
    return float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if len(pcm) else 0.0


def _sequence(pcm: np.ndarray) -> np.ndarray | None:
    """One take -> its (3, 96) template, centred on the take's energy."""
    f = _Features.get()
    pad = np.zeros(RATE // 2, np.int16)
    m = f.melspec(np.concatenate([pad, pcm, pad]))
    reach = max(OFFSETS)
    if len(m) < WIN + 2 * reach + 1:
        return None
    e = f.embed(np.array([m[i:i + WIN] for i in range(len(m) - WIN + 1)]))
    en = np.exp(m).sum(1)
    c = int((en * np.arange(len(en))).sum() / en.sum())   # energy centroid
    i = int(np.clip(c - WIN // 2, reach, len(e) - 1 - reach))
    return np.array([e[i + k] for k in OFFSETS])


def _match(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.sum(a * b, axis=1)))


def wav_to_pcm(wav: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


def enroll(takes: list[np.ndarray]) -> tuple[bool, str]:
    """Build templates from the owner's takes and save them. Rejects takes
    that are silent or don't sound like each other (a cough, a wrong word)."""
    seqs = []
    for pcm in takes:
        if _rms(pcm) < SPEECH_RMS * 0.5:
            return False, "one take was silent — the mic may not have caught it"
        s = _sequence(pcm)
        if s is None:
            return False, "one take was too short"
        seqs.append(s)
    worst = min(_match(seqs[i], seqs[j])
                for i in range(len(seqs)) for j in range(i + 1, len(seqs)))
    if worst < 0.85:
        return False, (f"the takes didn't sound alike (match {worst:.2f}); "
                       "try again, the same word each time")
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    np.save(TEMPLATES, np.array(seqs))
    return True, f"learned (takes agree at {worst:.2f})"


def enrolled() -> bool:
    return TEMPLATES.exists()


class Detector:
    """Streaming matcher: feed 16k int16 audio, get True when "Oziel" lands.
    Each 80ms hop computes only its new mel frames and new embeddings, and
    nothing at all while the room is quiet."""

    def __init__(self, templates: np.ndarray, threshold: float = THRESHOLD):
        self.T = templates
        self.threshold = threshold
        self.f = _Features.get()
        self.audio = np.zeros(0, np.int16)
        self.mel = np.zeros((0, 32), np.float32)
        self.emb: dict[int, np.ndarray] = {}   # absolute mel end-frame -> embedding
        self.frames = 0                        # absolute mel frames produced
        self.loud_until = -1                   # keep embedding until this frame
        self.deaf_until = 0.0
        self.best = 0.0                        # highest score seen (diagnostics)

    def feed(self, pcm: np.ndarray) -> bool:
        self.audio = np.concatenate([self.audio, pcm])
        hit = False
        while len(self.audio) >= HOP + OVERLAP:
            chunk = self.audio[:HOP + OVERLAP]
            self.audio = self.audio[HOP:]
            hit = self._hop(chunk) or hit
        return hit

    def _hop(self, chunk: np.ndarray) -> bool:
        new = self.f.melspec(chunk)
        start = self.frames
        self.frames += len(new)
        self.mel = np.concatenate([self.mel, new])[-(WIN + 40):]
        if _rms(chunk[OVERLAP:]) > SPEECH_RMS:
            self.loud_until = self.frames + WIN
        if self.frames > self.loud_until:
            self.emb.clear()                   # quiet room: no model calls
            return False
        base = self.frames - len(self.mel)     # absolute index of self.mel[0]
        ends = [e for e in range(start + 1, self.frames + 1)
                if e % 2 == 0 and e - WIN >= base]
        if not ends:
            return False
        wins = np.array([self.mel[e - WIN - base:e - base] for e in ends])
        for e, v in zip(ends, self.f.embed(wins)):
            self.emb[e] = v
        for k in [k for k in self.emb if k < self.frames - 60]:
            del self.emb[k]
        hit = False
        for e in ends:
            t = e - max(OFFSETS)               # the centre whose last window just landed
            seq = [self.emb.get(t + k) for k in OFFSETS]
            if any(v is None for v in seq):
                continue
            score = max(_match(np.array(seq), T) for T in self.T)
            self.best = max(self.best, score)
            if score >= self.threshold and time.monotonic() > self.deaf_until:
                self.deaf_until = time.monotonic() + COOLDOWN
                hit = True
        return hit


class WakeListener:
    """Owns the mic while Oziel sleeps; calls on_wake once, from its own thread."""

    def __init__(self, on_wake):
        self._on_wake = on_wake
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if not enrolled():
            return False
        if self.running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(2.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        det = Detector(np.load(TEMPLATES))
        heard = False
        with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                               blocksize=HOP) as mic:
            while not self._stop.is_set():
                data, _ = mic.read(HOP)
                if det.feed(np.frombuffer(bytes(data), np.int16)):
                    heard = True
                    break
        if heard:  # mic closed first, so the live session can take it
            self._on_wake()
