"""The Presence — Oziel's one window.

pywebview renders ui/app.html in a native window; this Api class is the
bridge JS calls into. Each bridge call runs on its own thread, so slow
things (TTS + playback) may block their call without freezing the UI.
"""
from pathlib import Path

import webview

from oziel import config
from oziel.audio import play_wav
from oziel.providers.mistral import MistralProvider

APP_HTML = Path(__file__).parent / "app.html"

GREETING = (
    "Hello. I'm Oziel. If you can hear me clearly, we're almost there. "
    "Tap yes, or tap play again."
)

VOICE_SAMPLES = {
    "en_paul_neutral": "This is my neutral voice. Steady and calm.",
    "en_paul_confident": "This is my confident voice. I get things done.",
    "en_paul_happy": "This is my happy voice. Every errand is a pleasure!",
}


class Api:
    def __init__(self):
        self.cfg = config.load()
        self.provider = (
            MistralProvider(self.cfg["api_key"]) if self.cfg["api_key"] else None
        )

    # ---- state ----
    def get_state(self) -> dict:
        return {
            "setup_complete": self.cfg["setup_complete"],
            "has_key": bool(self.cfg["api_key"]),
            "voice": self.cfg["voice"],
            "verbosity": self.cfg["verbosity"],
            "has_phrase": bool(self.cfg["confirm_phrase"]),
            "usage_usd": round(self.cfg["usage_usd"], 4),
        }

    def _spend(self):
        if self.provider:
            self.cfg["usage_usd"] += self.provider.last_call_usd
            config.save(self.cfg)

    # ---- setup steps ----
    def save_key(self, key: str) -> dict:
        key = key.strip()
        provider = MistralProvider(key)
        if not provider.validate_key():
            return {"ok": False, "error": "That key was rejected by Mistral."}
        self.cfg["api_key"] = key
        config.save(self.cfg)
        self.provider = provider
        return {"ok": True}

    def speak(self, text: str, voice: str | None = None) -> dict:
        if not self.provider:
            return {"ok": False, "error": "No API key yet."}
        try:
            wav = self.provider.tts(text, voice or self.cfg["voice"])
            self._spend()
            play_wav(wav)  # blocks this bridge thread only
            return {"ok": True}
        except Exception as e:  # surfaced in the UI caption area
            return {"ok": False, "error": str(e)}

    def greet(self) -> dict:
        return self.speak(GREETING)

    def preview_voice(self, slug: str) -> dict:
        return self.speak(VOICE_SAMPLES.get(slug, "Hello."), voice=slug)

    def choose_voice(self, slug: str) -> dict:
        self.cfg["voice"] = slug
        config.save(self.cfg)
        return {"ok": True}

    def complete_setup(self) -> dict:
        self.cfg["setup_complete"] = True
        config.save(self.cfg)
        self.speak(
            "Setup complete. My ears arrive in the next build. "
            "I can't wait to get to work."
        )
        return {"ok": True}

    # ---- settings ----
    def set_verbosity(self, level: str) -> dict:
        if level in ("brief", "chatty"):
            self.cfg["verbosity"] = level
            config.save(self.cfg)
        return {"ok": True}


def run():
    api = Api()
    webview.create_window(
        "Oziel",
        url=APP_HTML.as_uri(),
        js_api=api,
        width=420,
        height=680,
        resizable=False,
        background_color="#0b0d12",
    )
    webview.start()
