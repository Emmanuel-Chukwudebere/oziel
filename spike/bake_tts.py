"""Pre-bake Oziel's fixed setup lines into the TTS disk cache.

Gemini TTS runs ~4s per line; every line here is a fixed string, so one
bake makes setup speech instant on every run after. Dynamic lines (name
and phrase readbacks) still hit the API live.

Strings must byte-match the ones in app.html / window.py — a drifted copy
just means that line falls back to the API (slow, not broken).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oziel.ui.window import GREETING  # single source for the greeting
from oziel.providers.gemini import GeminiProvider

VOICES = ["Charon", "Puck", "Kore"]

PRE_PICK = [  # spoken before the voice step — always the default voice
    GREETING,
    "Now — what should I call you? Say your name.",
    "Okay — once more, slowly.",
]
PER_VOICE = [  # spoken after the pick, in whichever voice won
    "Good choice. This is my voice now.",
    "Last thing. Risky actions wait for a go phrase only you know. "
    "Think of one you'd never say by accident — then say it. Or say skip.",
    "Okay — say it again.",
    "Say it once more.",
    "And one last time.",
    "Locked in. That phrase stays between us.",
    "Skipping. Risky actions stay locked until we set it.",
    "That didn't match. Say again to retry — or say different.",
]
VOICE_LINES = {  # the audition — each voice introduces itself
    "Charon": "I'm Charon. Calm, deep, unhurried. Say use it — or say next.",
    "Puck": "I'm Puck! Bright and upbeat — every errand's an adventure. Say use it — or say next.",
    "Kore": "I'm Kore. Firm and clear. Consider it done. Say use it — or say next.",
}


def main():
    env = Path(__file__).resolve().parents[1] / ".env"
    key = [l.split("=", 1)[1].strip() for l in env.read_text().splitlines()
           if l.startswith("GEMINI_API_KEY=")][0]
    p = GeminiProvider(key)
    jobs = [(t, VOICES[0]) for t in PRE_PICK]
    jobs += [(t, v) for v in VOICES for t in PER_VOICE]
    jobs += [(VOICE_LINES[v], v) for v in VOICES]
    done = fail = 0
    for i, (text, voice) in enumerate(jobs, 1):
        t0 = time.perf_counter()
        try:
            p.tts(text, voice)
            done += 1
            took = time.perf_counter() - t0
            print(f"[{i}/{len(jobs)}] {voice}: {text[:44]!r} {took:.1f}s")
            if took > 0.05:      # real API call (not a cache hit) —
                time.sleep(8.0)  # pace under the free-tier TTS rate limit
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(jobs)}] {voice}: FAILED {e.__class__.__name__}")
            time.sleep(15)
    print(f"baked {done}, failed {fail}")


if __name__ == "__main__":
    main()
