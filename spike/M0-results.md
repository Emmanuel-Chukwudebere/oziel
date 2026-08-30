# M0 Spike Results — Day 1 (2026-08-30)

Measured from the owner's laptop and real network, against the owner's free-tier Mistral key.
Spike artifacts in `spike/out/` (throwaway — not product code).

## Verified working

- API key valid; 54 models visible, including everything the PRD needs:
  - Brain: `mistral-medium-3-5` (262k context, vision, reasoning, function calling)
  - STT batch: `voxtral-mini-latest`
  - STT realtime: `voxtral-mini-transcribe-realtime-2602` (websocket — not yet tested, see Pending)
  - TTS: `voxtral-mini-tts-2603`
- **Full audio loop proven:** TTS spoke "Hello Immanuel. The coding session just finished…" → fed back into the transcriber → came back word-perfect (only Immanuel→"Emmanuel" spelling, phonetically identical).
- TTS returns JSON `{"audio_data": "<base64>"}`, not raw bytes — decode before playback.
- Stock voices exist with **emotion variants** (e.g., `en_paul_neutral/_happy/_confident/_frustrated/_excited`) → Oziel can match tone to context. Custom voice cloning from a 2–3s sample also supported (saved voice profiles via `voice_id`).

## Measured latencies (fresh connection per call = worst case)

| Step | Measured | Notes |
|---|---|---|
| Brain (Medium 3.5) TTFT, warm | **1.10–1.17 s** | first-ever call 4.4 s (TLS/cold start — one-time) |
| Brain routing call, total | **1.0–1.3 s** | correct action-JSON on first attempt |
| TTS time-to-first-byte | **1.49–1.66 s** | docs say ~0.8 s for pcm; delta ≈ per-call TLS handshake |
| TTS full sentence, total | 2.1–2.5 s | pcm streaming lets playback start at TTFB |
| STT batch (4 s audio) | 2.0 s | worst case; realtime variant is the production path (sub-200 ms per docs) |

**Voice-loop projection** (end of speech → first spoken word):
- Naive batch pipeline: ~2.0 + 1.1 + 1.5 ≈ **4.6 s** — over budget; not the design.
- Production design (realtime STT streams while user talks + persistent connections + pcm streaming TTS): ~0.2 + ~0.7 + ~0.8 ≈ **1.7 s** — inside the 2 s budget, **to be proven in v1**. Every call above paid a fresh TLS handshake; the app will hold connections open.

## Free-tier limits (from response headers)

- **50 requests/minute**, **25,000 tokens/minute** — comfortable for one user.
- A routing call costs ~96 tokens (60 prompt + 36 completion).

## Cost model (pricing fetched 2026-08-30)

- Medium 3.5: $1.50/M input, $7.50/M output tokens
- Voxtral realtime STT: $0.006/min audio (batch: $0.003/min)
- Voxtral TTS: $0.016 per 1,000 characters

Per typical voice command (5 s speech, routing call, ~60-char reply): **~$0.002**.
100 commands/day ≈ $0.20/day ≈ **$6/month** — inside the $10 free credits.
Agent-heavy tasks (10-step UIA/browse with screenshots) ≈ $0.03 each; ten per day adds ~$0.30/day.
**Verdict: viable but not roomy on heavy months — the usage meter and ladder-first design (PRD §3.2, §6) are load-bearing, not decorative.**

## Pending M0 items

1. Realtime STT websocket test (needs a ws client; do during v1 build).
2. Custom "Oziel" wake-word model — train via openWakeWord synthetic pipeline, then measure false triggers over a day of room audio.
3. whisper-tiny accuracy on the owner's voice (offline pack, F8).
4. Confirm persistent-connection latencies hit the 1.7 s projection.
