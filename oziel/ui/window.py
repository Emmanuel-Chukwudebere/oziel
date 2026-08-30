"""The Presence — Oziel's one window.

pywebview renders ui/app.html in a native window; this Api class is the
bridge JS calls into. Each bridge call runs on its own thread, so slow
things (TTS + playback) may block their call without freezing the UI.
"""
from pathlib import Path

import webview

from oziel import config
from oziel.audio import play_wav, record_until_silence
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
            "user_name": self.cfg.get("user_name"),
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
        key = (key or "").strip()
        if not key:
            return {"ok": False, "error": "The field came through empty — the paste may not have landed. Try Ctrl+V again, or use the key found on this machine."}
        provider = MistralProvider(key)
        ok, reason = provider.validate_key()
        if not ok:
            return {"ok": False, "error": f"Key check failed ({len(key)} chars received). {reason}"}
        self.cfg["api_key"] = key
        config.save(self.cfg)
        self.provider = provider
        return {"ok": True}

    def _find_local_key(self) -> str | None:
        """Dev convenience: a .env at the repo root (never shipped in builds)."""
        env = Path(__file__).resolve().parents[2] / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("MISTRAL_API_KEY="):
                    return line.split("=", 1)[1].strip()
        return None

    def local_key_available(self) -> bool:
        return self._find_local_key() is not None

    def use_local_key(self) -> dict:
        key = self._find_local_key()
        if not key:
            return {"ok": False, "error": "No local key found."}
        return self.save_key(key)

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

    def record_and_transcribe(self, max_seconds: float = 10) -> dict:
        if not self.provider:
            return {"ok": False, "error": "No API key yet."}
        try:
            wav = record_until_silence(max_seconds=max_seconds)
            text = self.provider.stt(wav)
            self._spend()
            return {"ok": True, "text": text.strip()}
        except Exception as e:
            return {"ok": False, "error": f"Mic/STT problem: {e}"}

    def save_phrase(self, phrase: str) -> dict:
        phrase = (phrase or "").strip()
        if not phrase:
            return {"ok": False, "error": "Empty phrase."}
        self.cfg["confirm_phrase"] = phrase
        config.save(self.cfg)
        return {"ok": True}

    def chat_reply(self, user_text: str) -> dict:
        if not self.provider:
            return {"ok": False, "error": "No API key yet."}
        name = self.cfg.get("user_name") or "there"
        style = (
            "one short sentence"
            if self.cfg["verbosity"] == "brief"
            else "two or three warm sentences"
        )
        system = (
            f"You are Oziel, a voice assistant living on the Windows laptop of {name}. "
            f"Your reply is spoken aloud: {style}, plain text, no markdown or lists. "
            "You cannot take actions yet — your hands arrive in a coming build; "
            "be honest and lighthearted about that if asked to do something."
        )
        try:
            text = self.provider.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text},
                ],
                max_tokens=120,
            )
            self._spend()
            return {"ok": True, "text": text.strip()}
        except Exception as e:
            return {"ok": False, "error": f"Brain problem: {e}"}

    def save_name(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "I didn't catch a name — type it in."}
        self.cfg["user_name"] = name
        config.save(self.cfg)
        return {"ok": True}

    def complete_setup(self) -> dict:
        self.cfg["setup_complete"] = True
        config.save(self.cfg)
        name = self.cfg.get("user_name") or ""
        self.speak(
            f"Setup complete{', ' + name if name else ''}. "
            "I'm all ears — tap the mic anytime. My wake word comes soon."
        )
        return {"ok": True}

    def reset_setup(self) -> dict:
        """Wipe everything and start over — powers the Reset button in settings."""
        self.cfg = dict(config.DEFAULTS)
        config.save(self.cfg)
        self.provider = None
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
