"""Audio output. winsound covers v1 (whole-clip WAV playback through the
system-default device, which also means earbuds win automatically when
connected). Streaming PCM playback replaces this when the realtime voice
loop lands.
"""
import winsound


def play_wav(wav_bytes: bytes) -> None:
    """Blocks until playback finishes."""
    winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
