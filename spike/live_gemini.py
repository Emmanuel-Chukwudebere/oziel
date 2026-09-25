"""M1 spike: Gemini Live API — full-duplex realtime voice from this laptop.

Proves the walkie-talkie -> phone-call switch before Oziel's loop is rewired:
one websocket, mic streaming up while audio streams down, server-side VAD,
barge-in (interrupt Oziel mid-sentence and it stops).

Usage:
    python spike/live_gemini.py                # earbuds (true barge-in)
    python spike/live_gemini.py --speakers     # laptop speakers: mic gated
                                               #   during playback (no barge-in,
                                               #   but no self-echo loop either)
    python spike/live_gemini.py --max-min 3    # auto-stop (cellular guard)

Needs GEMINI_API_KEY in the repo-root .env. Ctrl+C to stop; prints latency
per reply, barge-in count, token usage, and est. cost on exit.
"""
import argparse
import asyncio
import base64
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import websockets

MODEL = "gemini-3.1-flash-live-preview"
FALLBACK_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
IN_RATE, OUT_RATE = 16_000, 24_000
BLOCK = 1_600  # 100ms of 16kHz mono int16
RMS_VOICE = 500  # int16 speech threshold, ~0.015 float

SYSTEM = (
    "You are Oziel, a realtime voice assistant on Emmanuel's Windows laptop. "
    "Replies are spoken: one or two short sentences, plain text. "
    "You cannot take actions yet — your hands arrive in a coming build; "
    "be honest and lighthearted about that if asked."
)


def load_key() -> str:
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    sys.exit("Add GEMINI_API_KEY=<your key> to .env (free key: aistudio.google.com/apikey)")


class Player:
    """24kHz int16 playback fed in chunks; clear() is the barge-in."""

    def __init__(self):
        self.buf = bytearray()
        self.lock = threading.Lock()
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

    def feed(self, pcm: bytes):
        with self.lock:
            self.buf.extend(pcm)

    def clear(self):
        with self.lock:
            self.buf.clear()

    def pending(self) -> int:
        with self.lock:
            return len(self.buf)


class State:
    def __init__(self):
        self.speech_end = None      # when the user last stopped talking
        self.turn_started = False   # first audio chunk of current reply seen
        self.latencies = []
        self.barge_ins = 0
        self.you = ""
        self.oziel = ""
        self.usage = {}


async def run(model: str, speakers: bool, max_min: float):
    key = load_key()
    state = State()
    loop = asyncio.get_running_loop()
    mic_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)

    last_voice = [0.0]

    def mic_cb(indata, _frames, _t, _status):
        pcm = bytes(indata)
        rms = float(np.sqrt(np.mean(np.square(
            np.frombuffer(pcm, dtype=np.int16).astype(np.float32)))))
        now = time.perf_counter()
        if rms > RMS_VOICE:
            last_voice[0] = now
            state.speech_end = None
            state.turn_started = False
        elif last_voice[0] and state.speech_end is None and now - last_voice[0] > 0.5:
            state.speech_end = last_voice[0] + 0.5
        try:
            loop.call_soon_threadsafe(mic_q.put_nowait, pcm)
        except Exception:
            pass  # queue full — drop the block, keep realtime

    player = Player()
    mic = sd.RawInputStream(
        samplerate=IN_RATE, channels=1, dtype="int16", blocksize=BLOCK, callback=mic_cb
    )

    setup = {"setup": {
        "model": f"models/{model}",
        "generationConfig": {"responseModalities": ["AUDIO"]},
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
    }}

    print(f"connecting — model {model} ...")
    t0 = time.perf_counter()
    async with websockets.connect(
        WS_URL,
        additional_headers=[("x-goog-api-key", key)],
        max_size=None,
    ) as ws:
        await ws.send(json.dumps(setup))
        first = json.loads(await ws.recv())
        if "setupComplete" not in first:
            sys.exit(f"setup failed: {json.dumps(first)[:300]}")
        print(f"connected in {time.perf_counter() - t0:.2f}s — talk now "
              f"({'speakers: mic gated during playback' if speakers else 'earbuds: barge-in live'})")

        player.stream.start()
        mic.start()
        stop = time.perf_counter() + max_min * 60

        async def sender():
            while time.perf_counter() < stop:
                chunk = await mic_q.get()
                if speakers and player.pending() > 0:
                    continue  # don't let Oziel hear itself
                await ws.send(json.dumps({"realtimeInput": {"audio": {
                    "data": base64.b64encode(chunk).decode(),
                    "mimeType": f"audio/pcm;rate={IN_RATE}",
                }}}))
            await ws.close()

        async def receiver():
            async for raw in ws:
                msg = json.loads(raw)
                sc = msg.get("serverContent") or {}
                if sc.get("interrupted"):
                    player.clear()
                    state.barge_ins += 1
                    print("  [barge-in — Oziel stopped talking]")
                for part in (sc.get("modelTurn") or {}).get("parts", []):
                    data = (part.get("inlineData") or {}).get("data")
                    if data:
                        if not state.turn_started and state.speech_end:
                            lat = time.perf_counter() - state.speech_end
                            state.latencies.append(lat)
                            print(f"  [reply in {lat:.2f}s]")
                        state.turn_started = True
                        player.feed(base64.b64decode(data))
                if sc.get("inputTranscription"):
                    state.you += sc["inputTranscription"].get("text", "")
                if sc.get("outputTranscription"):
                    state.oziel += sc["outputTranscription"].get("text", "")
                if sc.get("turnComplete"):
                    if state.you.strip():
                        print(f"  you:   {state.you.strip()}")
                    if state.oziel.strip():
                        print(f"  oziel: {state.oziel.strip()}")
                    state.you = state.oziel = ""
                if "usageMetadata" in msg:
                    state.usage = msg["usageMetadata"]
                if "goAway" in msg:
                    print(f"server goAway — time left {msg['goAway'].get('timeLeft')}")
                    return

        try:
            await asyncio.gather(sender(), receiver())
        except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
            pass
        finally:
            mic.stop()
            player.stream.stop()

    lat = state.latencies
    print("\n--- session stats ---")
    print(f"replies: {len(lat)}" + (
        f", latency avg {sum(lat)/len(lat):.2f}s, best {min(lat):.2f}s, worst {max(lat):.2f}s"
        if lat else ""))
    print(f"barge-ins: {state.barge_ins}")
    if state.usage:
        print(f"usage: {json.dumps(state.usage)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL,
                    help=f"live model id (fallback: {FALLBACK_MODEL})")
    ap.add_argument("--speakers", action="store_true",
                    help="laptop speakers: gate mic during playback (no barge-in)")
    ap.add_argument("--max-min", type=float, default=5.0,
                    help="auto-stop after N minutes (cellular data guard)")
    args = ap.parse_args()
    try:
        asyncio.run(run(args.model, args.speakers, args.max_min))
    except KeyboardInterrupt:
        print("\nstopped.")
