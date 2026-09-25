"""Gemini implementation of Oziel's model provider.

Same interface as MistralProvider (PRD principle 8 — swappable by config):
tts/stt/chat + validate_key. Everything rides the owner's personal Gemini
key, free tier — last_call_usd stays 0.0 to keep the credits meter honest.

The realtime loop (oziel/live.py) uses the Live API over websocket; this
module covers the batch calls: setup speech, one-shot questions, previews.
"""
import base64
import hashlib
import io
import os
import re
import time
import wave
from pathlib import Path

import requests

API = "https://generativelanguage.googleapis.com/v1beta"

# lite: measured 2026-09-01 — 3.5-flash threw 503s and adds ~1s for zero
# quality gain on one-line replies and short transcripts
CHAT_MODEL = "gemini-3.1-flash-lite"
TTS_MODEL = "gemini-3.1-flash-tts-preview"

# Gemini TTS measured ~4s per line vs Voxtral's 1.5s — but setup lines are
# fixed strings, so a disk cache makes every replay instant.
TTS_CACHE = Path(os.environ.get("APPDATA", Path.home())) / "Oziel" / "tts_cache"


class GeminiProvider:
    def __init__(self, api_key: str):
        self._session = requests.Session()
        # header, not query string — keys in URLs leak via logs and proxies
        self._session.headers["x-goog-api-key"] = api_key
        self.last_call_usd = 0.0  # free tier

    def _post(self, model: str, body: dict) -> dict:
        # free tier throws occasional 5xx and rate-limits bursts with 429 —
        # quiet retries (longer waits for 429) cover both
        for attempt in (1, 2, 3):
            r = self._session.post(
                f"{API}/models/{model}:generateContent",
                json=body,
                timeout=60,
            )
            if attempt == 3 or r.status_code < 429 or r.status_code == 431:
                break
            time.sleep(attempt * (10 if r.status_code == 429 else 1))
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _text(data: dict) -> str:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    def validate_key(self) -> tuple[bool, str]:
        """Returns (ok, human-readable reason when not ok)."""
        # cellular connections stall on first contact — try three times
        for attempt in (1, 2, 3):
            try:
                r = self._session.get(f"{API}/models?pageSize=1", timeout=20)
            except requests.RequestException as e:
                if attempt == 3:
                    return False, (
                        f"network problem after 3 tries: {e.__class__.__name__}. "
                        "Check the connection and tap again."
                    )
                time.sleep(1)
                continue
            if r.status_code == 200:
                return True, ""
            return False, f"Google answered HTTP {r.status_code}: {r.text[:120]}"
        return False, "unreachable"

    def tts(self, text: str, voice: str) -> bytes:
        """Text to WAV bytes (Gemini returns raw PCM; we add the header)."""
        key = hashlib.sha1(f"{TTS_MODEL}|{voice}|{text}".encode()).hexdigest()
        cached = TTS_CACHE / f"{key}.wav"
        if cached.exists():
            return cached.read_bytes()
        data = self._post(TTS_MODEL, {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                },
            },
        })
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(part["data"])
        m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
        rate = int(m.group(1)) if m else 24_000
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        wav = buf.getvalue()
        TTS_CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(wav)
        return wav

    def stt(self, wav_bytes: bytes) -> str:
        data = self._post(CHAT_MODEL, {
            "contents": [{"parts": [
                {"text": "Transcribe this audio exactly as spoken. "
                         "Reply with only the transcript, nothing else. "
                         "If there is no speech, reply with nothing."},
                {"inlineData": {
                    "mimeType": "audio/wav",
                    "data": base64.b64encode(wav_bytes).decode(),
                }},
            ]}],
            "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}},
        })
        return self._text(data).strip()

    def chat(self, messages: list[dict], max_tokens: int = 300) -> str:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user",
             "parts": [{"text": m["content"]}]}
            for m in messages if m["role"] != "system"
        ]
        body = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return self._text(self._post(CHAT_MODEL, body))
