"""Mistral implementation of Oziel's model provider.

All model traffic goes through this module so the provider can be swapped
by config alone (PRD principle 8). A single keep-alive Session is shared
across calls — each fresh TLS handshake measured ~0.5s in M0, which is
budget we can't spare.
"""
import base64

import requests

API = "https://api.mistral.ai/v1"

BRAIN_MODEL = "mistral-medium-3-5"
TTS_MODEL = "voxtral-mini-tts-2603"
STT_MODEL = "voxtral-mini-latest"

# TTS is billed per character ($0.016/1k), STT per audio minute, and the
# brain per token; tracked so the Presence credits meter reflects reality.
TTS_USD_PER_CHAR = 0.016 / 1000
STT_USD_PER_SECOND = 0.003 / 60
BRAIN_USD_PER_INPUT_TOKEN = 1.5 / 1_000_000
BRAIN_USD_PER_OUTPUT_TOKEN = 7.5 / 1_000_000


class MistralProvider:
    def __init__(self, api_key: str):
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"
        self.last_call_usd = 0.0

    def validate_key(self) -> tuple[bool, str]:
        """Returns (ok, human-readable reason when not ok)."""
        try:
            r = self._session.get(f"{API}/models", timeout=15)
        except requests.RequestException as e:
            return False, f"network problem: {e.__class__.__name__}"
        if r.status_code == 200:
            return True, ""
        return False, f"Mistral answered HTTP {r.status_code}: {r.text[:120]}"

    def tts(self, text: str, voice: str) -> bytes:
        """Text to WAV bytes."""
        r = self._session.post(
            f"{API}/audio/speech",
            json={
                "model": TTS_MODEL,
                "input": text,
                "voice": voice,
                "response_format": "wav",
            },
            timeout=60,
        )
        r.raise_for_status()
        self.last_call_usd = len(text) * TTS_USD_PER_CHAR
        return base64.b64decode(r.json()["audio_data"])

    def stt(self, wav_bytes: bytes) -> str:
        r = self._session.post(
            f"{API}/audio/transcriptions",
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": STT_MODEL},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        secs = data.get("usage", {}).get("prompt_audio_seconds", 0)
        self.last_call_usd = secs * STT_USD_PER_SECOND
        return data["text"]

    def chat(self, messages: list[dict], max_tokens: int = 300) -> str:
        r = self._session.post(
            f"{API}/chat/completions",
            json={
                "model": BRAIN_MODEL,
                "messages": messages,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        self.last_call_usd = (
            usage.get("prompt_tokens", 0) * BRAIN_USD_PER_INPUT_TOKEN
            + usage.get("completion_tokens", 0) * BRAIN_USD_PER_OUTPUT_TOKEN
        )
        return data["choices"][0]["message"]["content"]
