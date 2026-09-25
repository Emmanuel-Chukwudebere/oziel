"""Gemini Live — Oziel's realtime, full-duplex voice loop.

One websocket per conversation: mic audio streams up continuously while
Oziel's voice streams down. Google's server-side VAD decides turns, and a
barge-in (owner talks over Oziel) arrives as `interrupted` — playback is
flushed instantly so Oziel shuts up and listens.

The session runs its own asyncio loop on a daemon thread; UI updates flow
through a single on_event callback (thread-safe on the caller's side).
Events: {"type": "state", "value": connecting|listening|speaking}
        {"type": "you"|"oziel", "text": ...}   (transcripts, accumulating)
        {"type": "turn"} {"type": "interrupted"} {"type": "notice", ...}
        {"type": "error", "text": ...} {"type": "closed"}
"""
import asyncio
import base64
import json
import sys
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd
import websockets

MODEL = "gemini-3.1-flash-live-preview"
WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
IN_RATE, OUT_RATE = 16_000, 24_000
BLOCK = 1_600  # 100ms of 16kHz mono int16

# local speech gate: silence never leaves the laptop (it was ~156 MB/hour of
# uplink on cellular, and a backed-up queue dropped real speech with it)
GATE_FLOOR = 0.012 * 32768  # RMS a chunk must beat to count as speech
GATE_OVER_NOISE = 3.0       # ...or this many times the room's noise floor
PRE_ROLL = 3                # chunks (300ms) replayed so first syllables land
HANGOVER = 7                # chunks (700ms) kept after speech so the server's
                            # VAD still hears the pause that ends the turn
COALESCE = 5                # a stalled link sends up to 500ms per message
VOICE_WIN = 1_024           # samples per orb voice frame (~43ms at 24k)
VOICE_HZ = 30               # orb voice frames per second while Oziel talks


class _Player:
    """24kHz int16 playback fed in chunks; clear() is the barge-in."""

    def __init__(self):
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.recent = np.zeros(VOICE_WIN, np.int16)  # last ~43ms sent to the speaker
        self.stream = sd.RawOutputStream(
            samplerate=OUT_RATE, channels=1, dtype="int16", callback=self._cb
        )

    def _cb(self, outdata, frames, _t, _status):
        need = frames * 2
        with self.lock:
            chunk = bytes(self.buf[:need])
            del self.buf[: len(chunk)]
        outdata[: len(chunk)] = chunk
        outdata[len(chunk):] = b"\x00" * (need - len(chunk))
        played = np.frombuffer(bytes(outdata), np.int16)
        with self.lock:
            self.recent = np.concatenate([self.recent, played])[-VOICE_WIN:]

    def feed(self, pcm: bytes):
        with self.lock:
            self.buf.extend(pcm)

    def clear(self):
        with self.lock:
            self.buf.clear()

    def pending(self) -> int:
        with self.lock:
            return len(self.buf)

    def playing(self) -> np.ndarray:
        with self.lock:
            return self.recent.copy()


def voice_frame(pcm: np.ndarray, rate: int) -> tuple[float, float]:
    """(loudness 0..1, pitch Hz or 0) of one short slice — what the orb moves to.
    Pitch is plain autocorrelation over 70–400 Hz; unvoiced slices report 0."""
    x = pcm.astype(np.float32) / 32768
    rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
    if rms < 0.004:
        return 0.0, 0.0
    x = x - x.mean()
    n = len(x)
    spec = np.fft.rfft(x, 2 * n)
    r = np.fft.irfft(spec * np.conj(spec))[:n]
    lo, hi = rate // 400, min(n - 1, rate // 70)
    k = lo + int(np.argmax(r[lo:hi]))
    f0 = rate / k if r[0] > 0 and r[k] / r[0] > 0.35 else 0.0
    return min(1.0, rms * 5), round(f0, 1)


class LiveSession:
    def __init__(self, api_key: str, system: str, on_event,
                 echo_guard: bool = False, model: str = MODEL,
                 voice: str | None = None, tools: list | None = None,
                 on_tool=None, kickoff: str | None = None,
                 idle_sleep: float | None = None):
        self._key = api_key
        self._system = system
        self._emit = on_event
        self._echo_guard = echo_guard
        self._model = model
        self._voice = voice
        self._tools = tools          # functionDeclarations, or None
        self._on_tool = on_tool      # callable(name, args) -> dict, any thread
        self._kickoff = kickoff      # hidden first turn so Oziel speaks first
        self._stop_req = threading.Event()
        self._muted = threading.Event()
        self._thread: threading.Thread | None = None
        self.dropped = 0  # mic chunks discarded because upload stalled
        self.sent_bytes = 0  # uplink payload, for the usage meter
        self._idle_sleep = idle_sleep  # seconds of quiet before Oziel sleeps
        self._sleep_req = threading.Event()  # finish this turn, then sleep
        self._player: _Player | None = None

    def sleep_after_turn(self):
        """Voice mute: let Oziel finish its short goodbye, then close."""
        self._sleep_req.set()

    def wait_quiet(self, timeout: float = 8.0):
        """Block (any thread) until Oziel's current reply has finished playing."""
        end = time.monotonic() + timeout
        time.sleep(0.4)  # the reply may still be arriving
        while time.monotonic() < end:
            p = self._player
            if p is None or p.pending() == 0:
                time.sleep(0.3)
                if p is None or p.pending() == 0:
                    return
            time.sleep(0.1)

    def join(self, timeout: float = 3.0):
        """Wait for the session thread — call before interpreter shutdown so
        PortAudio callbacks never fire into a dying interpreter."""
        if self._thread:
            self._thread.join(timeout)

    def set_muted(self, muted: bool):
        """Mute stops mic upload (privacy + cellular data); session stays up."""
        if muted:
            self._muted.set()
        else:
            self._muted.clear()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_req.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            asyncio.run(self._main())
        except Exception as e:
            self._emit({"type": "error", "text": f"Live session: {e}"})
        finally:
            self._emit({"type": "closed"})

    async def _main(self):
        loop = asyncio.get_running_loop()
        mic_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
        player = _Player()
        self._player = player
        speaking = False
        last_active = loop.time()
        turn_done = asyncio.Event()  # a reply finished since sleep was asked

        def _enqueue(pcm: bytes):
            # runs on the loop thread; if upload has stalled, drop the OLDEST
            # audio so what Oziel hears stays live instead of lagging behind
            while mic_q.full():
                try:
                    mic_q.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:
                    break
            mic_q.put_nowait(pcm)

        def mic_cb(indata, _frames, _t, _status):
            try:
                loop.call_soon_threadsafe(_enqueue, bytes(indata))
            except RuntimeError:
                pass  # loop already closed

        mic = sd.RawInputStream(
            samplerate=IN_RATE, channels=1, dtype="int16",
            blocksize=BLOCK, callback=mic_cb,
        )
        gen_config = {"responseModalities": ["AUDIO"]}
        if self._voice:
            gen_config["speechConfig"] = {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}}
            }
        setup = {"setup": {
            "model": f"models/{self._model}",
            "generationConfig": gen_config,
            "systemInstruction": {"parts": [{"text": self._system}]},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }}
        if self._tools:
            setup["setup"]["tools"] = [{"functionDeclarations": self._tools}]

        self._emit({"type": "state", "value": "connecting"})
        # key in a handshake header, not the URL — keeps it out of logs
        async with websockets.connect(
            WS_URL,
            additional_headers=[("x-goog-api-key", self._key)],
            max_size=None,
            # library defaults: a tight ping timeout kills healthy sessions
            # on a laggy cellular link, and each drop replays the greeting
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            await ws.send(json.dumps(setup))
            first = json.loads(await ws.recv())
            if "setupComplete" not in first:
                raise RuntimeError(f"setup rejected: {json.dumps(first)[:200]}")
            player.stream.start()
            mic.start()
            self._emit({"type": "state", "value": "listening"})
            if self._kickoff:
                await ws.send(json.dumps({"clientContent": {
                    "turns": [{"role": "user",
                               "parts": [{"text": self._kickoff}]}],
                    "turnComplete": True,
                }}))
            you, oziel = "", ""

            async def sender():
                nonlocal last_active
                pre = deque(maxlen=PRE_ROLL)
                noise, hang, open_ = GATE_FLOOR / GATE_OVER_NOISE, 0, False

                async def send(payload: dict):
                    msg = json.dumps({"realtimeInput": payload})
                    self.sent_bytes += len(msg)
                    await ws.send(msg)

                while not self._stop_req.is_set():
                    try:
                        chunks = [await asyncio.wait_for(mic_q.get(), timeout=0.2)]
                    except asyncio.TimeoutError:
                        continue
                    while len(chunks) < COALESCE and not mic_q.empty():
                        chunks.append(mic_q.get_nowait())
                    if self._muted.is_set() or (
                            self._echo_guard and player.pending() > 0):
                        # muted, or speakers mode keeping Oziel from hearing
                        # itself: nothing goes up, and the gate starts fresh
                        pre.clear()
                        hang = 0
                        continue
                    out = []
                    for c in chunks:
                        rms = float(np.sqrt(np.mean(
                            np.square(np.frombuffer(c, np.int16).astype(np.float32)))))
                        # room floor: falls to any quieter chunk at once, rises
                        # slowly — a steady fan is learned in seconds, speech
                        # (which keeps pausing) never is
                        noise = rms if rms < noise else noise + 0.01 * (rms - noise)
                        if rms > max(GATE_FLOOR, noise * GATE_OVER_NOISE):
                            if not open_:
                                out.extend(pre)  # the syllable before the trigger
                                pre.clear()
                                open_ = True
                            hang = HANGOVER
                            out.append(c)
                        elif hang > 0:
                            hang -= 1
                            out.append(c)
                        else:
                            pre.append(c)
                    if out:
                        last_active = loop.time()
                        a, f0 = voice_frame(np.frombuffer(out[-1], np.int16), IN_RATE)
                        self._emit({"type": "voice", "who": "you", "a": a, "f": f0})
                        await send({"audio": {
                            "data": base64.b64encode(b"".join(out)).decode(),
                            "mimeType": f"audio/pcm;rate={IN_RATE}",
                        }})
                    if open_ and hang == 0:
                        self._emit({"type": "voice", "who": "you", "a": 0, "f": 0})
                        # tell the server the stream paused, so its VAD
                        # closes the turn instead of waiting for more audio
                        open_ = False
                        await send({"audioStreamEnd": True})
                mic.stop()  # stop capturing before the close handshake
                await ws.close()

            async def receiver():
                nonlocal you, oziel, speaking, last_active
                async for raw in ws:
                    msg = json.loads(raw)
                    sc = msg.get("serverContent") or {}
                    if sc.get("interrupted"):
                        player.clear()
                        self._emit({"type": "interrupted"})
                    for part in (sc.get("modelTurn") or {}).get("parts", []):
                        data = (part.get("inlineData") or {}).get("data")
                        if data:
                            player.feed(base64.b64decode(data))
                            if not speaking:
                                speaking = True
                                self._emit({"type": "state", "value": "speaking"})
                    if sc.get("inputTranscription"):
                        you += sc["inputTranscription"].get("text", "")
                        self._emit({"type": "you", "text": you.strip()})
                    if sc.get("outputTranscription"):
                        oziel += sc["outputTranscription"].get("text", "")
                        self._emit({"type": "oziel", "text": oziel.strip()})
                    if sc or "toolCall" in msg:
                        last_active = loop.time()
                    if sc.get("turnComplete"):
                        if self._sleep_req.is_set():
                            turn_done.set()
                        self._emit({"type": "turn"})
                        you, oziel = "", ""
                    if "toolCall" in msg and self._on_tool:
                        responses = []
                        for c in msg["toolCall"].get("functionCalls", []):
                            result = await loop.run_in_executor(
                                None, self._on_tool,
                                c.get("name"), c.get("args") or {})
                            responses.append({
                                "id": c.get("id"),
                                "name": c.get("name"),
                                "response": result or {"ok": True},
                            })
                        await ws.send(json.dumps(
                            {"toolResponse": {"functionResponses": responses}}))
                    if "goAway" in msg:
                        self._emit({"type": "notice",
                                    "text": "session ending soon"})

            async def watcher():
                # flip the orb back to listening once playback drains;
                # surface upload stalls so a slow link is visible on screen
                nonlocal speaking
                seen_drops, last_warn = 0, 0.0
                asked = None  # loop time sleep was requested
                lag = player.stream.latency  # speaker delay: frames land on time
                while not self._stop_req.is_set():
                    await asyncio.sleep(1 / VOICE_HZ)
                    if speaking:
                        a, f0 = voice_frame(player.playing(), OUT_RATE)
                        loop.call_later(lag, self._emit,
                                        {"type": "voice", "who": "oz", "a": a, "f": f0})
                    if self._sleep_req.is_set():
                        now = loop.time()
                        asked = asked or now
                        # goodbye spoken and played out; the long fallback only
                        # covers a turn that never completes
                        if (turn_done.is_set() and player.pending() == 0) or now - asked > 20:
                            self._emit({"type": "sleep", "why": "asked"})
                            self._stop_req.set()
                            break
                    elif (self._idle_sleep and not speaking
                          and loop.time() - last_active > self._idle_sleep):
                        self._emit({"type": "sleep", "why": "idle"})
                        self._stop_req.set()
                        break
                    if speaking and player.pending() == 0:
                        speaking = False
                        self._emit({"type": "state", "value": "listening"})
                    if self.dropped > seen_drops:
                        seen_drops = self.dropped
                        now = loop.time()
                        if now - last_warn > 3:
                            last_warn = now
                            self._emit({"type": "notice",
                                        "text": "network slow, dropping audio"})

            try:
                await asyncio.gather(sender(), receiver(), watcher())
            except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
                pass
            finally:
                self._stop_req.set()
                mic.stop()
                mic.close()
                player.stream.stop()
                player.stream.close()
                print(f"[live] uplink: {self.sent_bytes / 1e6:.2f} MB",
                      file=sys.stderr)
                if self.dropped:
                    print(f"[live] upload stalled: {self.dropped} mic chunks "
                          f"({self.dropped / 10:.1f}s) dropped", file=sys.stderr)
