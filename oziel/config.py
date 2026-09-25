"""Oziel's single source of truth for settings.

Both the Presence window and voice commands edit this same file, so a
change made anywhere is visible everywhere.
"""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Oziel"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS = {
    "user_name": None,        # what Oziel calls the owner
    "api_key": None,          # Mistral (dormant fallback provider)
    "voice": "Charon",        # Gemini prebuilt voice — used by TTS and Live alike
    "confirm_phrase": None,   # captured by voice during setup; risky actions are
                              # fully blocked while this is None
    "verbosity": "brief",     # brief | chatty
    "gemini_api_key": None,   # realtime voice loop; falls back to .env GEMINI_API_KEY
    "echo_guard": False,      # True = mute mic while Oziel talks (laptop speakers);
                              # False = full duplex with barge-in (earbuds)
    "setup_complete": False,
    "usage_month": None,      # e.g. "2026-08"
    "usage_usd": 0.0,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass  # corrupted config falls back to defaults; setup will re-run
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
