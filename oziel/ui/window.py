"""The Presence — Oziel's one window.

Live-first: after the key step, the app IS a realtime voice session
(Gemini Live, speech-to-speech). Setup happens inside that session —
Oziel conducts it conversationally and calls tools (save_name, set_voice,
save_phrase, finish_setup) as facts land. The same tool plumbing is what
the hands (executor ladder) plug into later.

pywebview renders ui/app.html; this Api class is the JS bridge. Each
bridge call runs on its own thread; live-session events flow back to JS
through window.evaluate_js.
"""
import json
import queue
import threading
import time
from pathlib import Path

import webview

from oziel import config
from oziel.live import LiveSession
from oziel.providers.gemini import GeminiProvider

APP_HTML = Path(__file__).parent / "app.html"

VOICES = {
    "Charon": "calm and deep",
    "Puck": "bright and upbeat",
    "Kore": "firm and clear",
}

# who Oziel is when asked: the prompt used to say "Windows laptop" and
# nothing about origin, so the model filled the gap with Microsoft
ORIGIN = (
    "If asked who made you: your owner built you as a personal project, "
    "hand-written code on this laptop. Your voice and reasoning run on "
    "Google's Gemini live model. Microsoft only makes the Windows this "
    "laptop runs — never say Microsoft, or any company, built you. "
)

SETUP_TOOLS = [
    {
        "name": "save_name",
        "description": (
            "Save the OWNER's name once they have said it and confirmed it. "
            "Never 'Oziel' — that is your own name, not theirs."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"name": {"type": "STRING"}},
            "required": ["name"],
        },
    },
    {
        "name": "set_voice",
        "description": (
            "Persist the owner's voice choice. Calling this with a voice other "
            "than the current one switches the session to that voice within a "
            "couple of seconds — the new voice then introduces itself."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"voice": {
                "type": "STRING",
                "enum": list(VOICES.keys()),
            }},
            "required": ["voice"],
        },
    },
    {
        "name": "save_phrase",
        "description": (
            "Save the owner's go-phrase for risky actions. Only call after the "
            "owner said the phrase twice and both matched."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"phrase": {"type": "STRING"}},
            "required": ["phrase"],
        },
    },
    {
        "name": "finish_setup",
        "description": "Mark first-run setup complete. Call once every step is done or skipped.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


class Api:
    def __init__(self):
        self.cfg = config.load()
        key = self._gemini_key()
        self.provider = GeminiProvider(key) if key else None
        self._live: LiveSession | None = None
        self._window = None  # set by run() before webview.start()
        self._muted = False
        # live events -> UI on a dedicated thread. evaluate_js is a blocking
        # round-trip into WebView2 (tens of ms); calling it from the live
        # session's asyncio loop stalled the mic uploader and dropped audio.
        self._ui_q: queue.Queue = queue.Queue()
        self._ui_pump = threading.Thread(target=self._drain_ui, daemon=True)

    # ---- state ----
    def get_state(self) -> dict:
        return {
            "setup_complete": self.cfg["setup_complete"],
            # setup gate: only a key saved through setup counts, so a wiped
            # config always starts from the key step (.env shows as a card)
            "has_key": bool(self.cfg.get("gemini_api_key")),
            "user_name": self.cfg.get("user_name"),
            "voice": self.cfg["voice"],
            "verbosity": self.cfg["verbosity"],
            "has_phrase": bool(self.cfg["confirm_phrase"]),
            "echo_guard": bool(self.cfg.get("echo_guard")),
            "muted": self._muted,
        }

    # ---- key ----
    def save_key(self, key: str) -> dict:
        key = (key or "").strip()
        if not key:
            return {"ok": False, "error": "The field came through empty — the paste may not have landed. Try Ctrl+V again, or use the key found on this machine."}
        provider = GeminiProvider(key)
        ok, reason = provider.validate_key()
        if not ok:
            return {"ok": False, "error": f"Key check failed ({len(key)} chars received). {reason}"}
        self.cfg["gemini_api_key"] = key
        config.save(self.cfg)
        self.provider = provider
        return {"ok": True}

    def _env_value(self, name: str) -> str | None:
        """Dev convenience: a .env at the repo root (never shipped in builds)."""
        env = Path(__file__).resolve().parents[2] / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith(name + "=") and line.split("=", 1)[1].strip():
                    return line.split("=", 1)[1].strip()
        return None

    def local_key_available(self) -> bool:
        return self._env_value("GEMINI_API_KEY") is not None

    def use_local_key(self) -> dict:
        key = self._env_value("GEMINI_API_KEY")
        if not key:
            return {"ok": False, "error": "No local key found."}
        return self.save_key(key)

    def _gemini_key(self) -> str | None:
        return self.cfg.get("gemini_api_key") or self._env_value("GEMINI_API_KEY")

    # ---- the live session ----
    def _system_prompt(self) -> tuple[str, list | None, str | None]:
        """Returns (system, tools, kickoff) for the current phase."""
        name = self.cfg.get("user_name")
        voice = self.cfg["voice"]
        style = ("one short sentence" if self.cfg["verbosity"] == "brief"
                 else "two or three warm sentences")
        if self.cfg["setup_complete"]:
            system = (
                f"You are Oziel, a realtime voice assistant on the Windows laptop "
                f"of {name or 'your owner'}. Ozi means errand in Igbo. "
                f"Replies are spoken: {style}, plain text. "
                + ORIGIN +
                "You cannot take actions yet — your hands arrive in a coming "
                "build; be honest and lighthearted about that if asked."
            )
            return system, None, None
        voices = "; ".join(f"{v} ({d})" for v, d in VOICES.items())
        done = []
        if name:
            done.append(f"name already saved: {name}")
        if self.cfg["confirm_phrase"]:
            done.append("go-phrase already saved")
        system = (
            "You are Oziel, a brand-new voice assistant being set up on your "
            "owner's Windows laptop. Ozi means errand in Igbo. This is a live "
            "voice conversation. Speak in short, warm sentences — one or two.\n"
            "Conduct first-run setup, one step at a time, skipping any step "
            "already done:\n"
            "1. Greet and make sure they can hear you clearly.\n"
            "2. Ask what to call them. Repeat it back; when they confirm, call "
            "save_name with THEIR name. Your name is Oziel; the owner's is not.\n"
            f"3. Voice: you currently speak as {voice}. The options are {voices}. "
            "Offer to keep this voice or hear another. If they pick a different "
            "one, call set_voice — the switch takes a couple of seconds and the "
            "new voice introduces itself and asks if it's a keeper. When they "
            "settle, call set_voice with the final choice.\n"
            "4. Go-phrase: explain that risky actions — deleting, sending, "
            "paying — stay locked behind a spoken phrase only they know. Have "
            "them say one they'd never say by accident, then have them repeat "
            "it; if both match, call save_phrase with the exact phrase. They "
            "may skip; actions stay locked until it's set.\n"
            "5. Call finish_setup, then tell them: just talk anytime; your "
            "hands — real actions — arrive in a coming build.\n"
            "Never call a tool for something the owner hasn't confirmed."
            + (" Already done: " + "; ".join(done) + "." if done else "")
        )
        kickoff = ("Begin setup now: greet your owner and check they can "
                   "hear you clearly.")
        return system, SETUP_TOOLS, kickoff

    def start_live(self) -> dict:
        key = self._gemini_key()
        if not key:
            return {"ok": False, "error": "No Gemini key found — add GEMINI_API_KEY to .env."}
        if self._live and self._live.running:
            self._live.stop()
        system, tools, kickoff = self._system_prompt()
        self._muted = False
        self._live = LiveSession(
            key, system, self._push_live,
            echo_guard=bool(self.cfg.get("echo_guard")),
            voice=self.cfg.get("voice"),
            tools=tools,
            on_tool=self._on_tool,
            kickoff=kickoff,
        )
        self._live.start()
        return {"ok": True}

    def stop_live(self) -> dict:
        if self._live:
            self._live.stop()
        return {"ok": True}

    def shutdown(self, *_):
        """Window closed: end the session and wait for its thread, so the
        interpreter never exits with PortAudio callbacks still live."""
        if self._live:
            self._live.stop()
            self._live.join(3.0)
        self._ui_q.put(None)

    def set_muted(self, muted: bool) -> dict:
        self._muted = bool(muted)
        if self._live:
            self._live.set_muted(self._muted)
        return {"ok": True, "muted": self._muted}

    def _restart_live_soon(self):
        """Stop the session after the tool response flushes; the UI's
        auto-reconnect brings it back with the updated config."""
        def _later():
            time.sleep(0.6)
            if self._live:
                self._live.stop()
        threading.Thread(target=_later, daemon=True).start()

    def _on_tool(self, name: str, args: dict) -> dict:
        if name == "save_name":
            owner = str(args.get("name", "")).strip()
            if not owner or owner.lower().strip(".!") == "oziel":
                return {"ok": False, "error": "That is your own name. Ask the "
                        "owner what to call them and confirm before saving."}
            self.cfg["user_name"] = owner
            config.save(self.cfg)
            self._push_live({"type": "setup", "step": "name"})
            return {"ok": True, "saved": self.cfg["user_name"]}
        if name == "set_voice":
            voice = args.get("voice")
            if voice not in VOICES:
                return {"ok": False, "error": f"unknown voice {voice!r}"}
            switching = voice != self.cfg["voice"]
            self.cfg["voice"] = voice
            config.save(self.cfg)
            self._push_live({"type": "setup", "step": "voice"})
            if switching:
                self._restart_live_soon()
                return {"ok": True, "note": "switching now — the session will "
                        "reconnect in the new voice"}
            return {"ok": True, "note": "keeping this voice"}
        if name == "save_phrase":
            phrase = str(args.get("phrase", "")).strip()
            if not phrase:
                return {"ok": False, "error": "empty phrase"}
            self.cfg["confirm_phrase"] = phrase
            config.save(self.cfg)
            self._push_live({"type": "setup", "step": "phrase"})
            return {"ok": True}
        if name == "finish_setup":
            self.cfg["setup_complete"] = True
            config.save(self.cfg)
            self._push_live({"type": "setup", "step": "done"})
            return {"ok": True}
        return {"ok": False, "error": f"unknown tool {name!r}"}

    def _push_live(self, evt: dict) -> None:
        """Any thread -> UI, never blocking the caller."""
        self._ui_q.put(evt)

    def _drain_ui(self) -> None:
        """One evaluate_js per burst: batch what is waiting, and keep only the
        newest of each streaming event so a slow webview never backs up."""
        newest_only = ("you", "oziel", "state")
        while True:
            evt = self._ui_q.get()
            done = evt is None
            batch = [] if done else [evt]
            while True:
                try:
                    nxt = self._ui_q.get_nowait()
                except queue.Empty:
                    break
                if nxt is None:
                    done = True
                    break
                batch.append(nxt)
            last = {}
            for i, e in enumerate(batch):
                if e.get("type") in newest_only:
                    last[e["type"]] = i
            batch = [e for i, e in enumerate(batch)
                     if e.get("type") not in newest_only or last[e["type"]] == i]
            if batch and self._window:
                js = ";".join(f"liveEvent({json.dumps(e)})" for e in batch)
                try:
                    self._window.evaluate_js(js)
                except Exception:
                    pass  # window closing mid-session
            if done:
                return

    # ---- settings ----
    def choose_voice(self, slug: str) -> dict:
        if slug in VOICES:
            self.cfg["voice"] = slug
            config.save(self.cfg)
            if self._live and self._live.running:
                self._restart_live_soon()
        return {"ok": True}

    def set_verbosity(self, level: str) -> dict:
        if level in ("brief", "chatty"):
            self.cfg["verbosity"] = level
            config.save(self.cfg)
        return {"ok": True}

    def set_echo_guard(self, on: bool) -> dict:
        self.cfg["echo_guard"] = bool(on)
        config.save(self.cfg)
        if self._live and self._live.running:
            self._restart_live_soon()
        return {"ok": True}

    def reset_setup(self) -> dict:
        """Wipe everything and start over — powers the Reset button in settings."""
        if self._live:
            self._live.stop()
        self.cfg = dict(config.DEFAULTS)
        config.save(self.cfg)
        self.provider = None
        return {"ok": True}


def _fit_client(window):
    """The window frame steals ~14x37px, so a 800x600 window renders the page
    at 786x563 — the design's bands then fight for the missing pixels. Grow
    the window by whatever the frame took, once, after the page loads."""
    try:
        inner = window.evaluate_js("[innerWidth, innerHeight]")
    except Exception:
        return
    if not inner:
        return
    w, h = inner
    if w and h and (w, h) != (800, 600):
        window.resize(800 + (800 - w), 600 + (600 - h))


def run():
    api = Api()
    api._window = webview.create_window(
        "Oziel",
        url=APP_HTML.as_uri(),
        js_api=api,
        width=800,
        height=600,
        resizable=False,
        background_color="#101010",
    )
    api._window.events.loaded += lambda: _fit_client(api._window)
    api._window.events.closed += api.shutdown
    api._ui_pump.start()
    webview.start()
    api.shutdown()  # belt and braces if the closed event never fired
