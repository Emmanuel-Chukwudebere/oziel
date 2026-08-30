"""Audio I/O.

Output: winsound covers v1 (whole-clip WAV playback through the
system-default device, which also means earbuds win automatically when
connected). Streaming PCM playback replaces this when the realtime voice
loop lands.

Input: block-based capture from the default mic with a simple RMS
silence detector — records until the speaker stops. Realtime streaming
STT replaces the record-then-send shape later; the mic handling stays.
"""
import io
import wave

import numpy as np
import sounddevice as sd
import winsound

SAMPLE_RATE = 16_000


def play_wav(wav_bytes: bytes) -> None:
    """Blocks until playback finishes."""
    winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)


def record_until_silence(
    max_seconds: float = 10.0,
    trailing_silence: float = 0.9,
    threshold: float = 0.012,
) -> bytes:
    """Record the default mic until the speaker goes quiet; returns WAV bytes.

    Never blocks past max_seconds, so a silent room can't hang the UI.
    """
    block = 0.1
    blocks: list[np.ndarray] = []
    heard = False
    silence = 0.0
    elapsed = 0.0
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        while elapsed < max_seconds:
            data, _ = stream.read(int(SAMPLE_RATE * block))
            blocks.append(data.copy())
            elapsed += block
            rms = float(np.sqrt(np.mean(np.square(data))))
            if rms > threshold:
                heard = True
                silence = 0.0
            elif heard:
                silence += block
                if silence >= trailing_silence:
                    break
    audio = np.concatenate(blocks)[:, 0]
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
