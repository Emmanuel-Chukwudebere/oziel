# Oziel

> *Ozi* means **message/errand** in Igbo. The *-el* makes it an angel.
> **Oziel — a voice that runs my computer.**

Oziel is a voice-first assistant for Windows. You talk to it — through the laptop mic, or earbuds from another room — and it acts: runs commands, manages files, operates apps and the browser, and **conducts AI coding sessions** (Claude Code) so you can direct software development by voice while away from the keyboard.

## How it's built

- **Thin client:** the laptop is the ears, mouth, and hands (<1 GB resident, near-zero idle CPU). The brain is [Mistral](https://mistral.ai)'s API — Voxtral realtime STT, Voxtral TTS, and Mistral Medium 3.5 — behind a swappable provider interface.
- **The action ladder:** deterministic before intelligent. OS commands → hotkeys → deep links → Windows UI Automation → vision. Cheap, reliable methods always run before model calls.
- **Self-writing skills:** every task Oziel figures out once becomes a replayable skill file — it gets faster (and cheaper) with use.
- **The conductor:** coding-agent sessions run on an Oziel-hosted pseudo-terminal, always visible in a real terminal window. You can type into the same session at any time — human input always wins.
- **Consent for consequences:** risky actions (send, pay, delete) require a spoken go-phrase only the owner knows. No default exists.
- **Offline fallback:** no internet → wake word + local whisper-tiny + a fixed command grammar keep the basics working.

## Status

Building in public, daily. Day 1 (2026-08-30): full audio loop proven — Oziel spoke its first words and transcribed them back word-perfect. Measured numbers in [`spike/M0-results.md`](spike/M0-results.md).

The full product spec is in [`PRD.md`](PRD.md).

## Roadmap

- **M0** — measurement spike ✅ (partial: wake-word training pending)
- **v1** — the voice loop: wake word → realtime STT → brain → action → spoken reply
- **v1.5** — offline fallback pack
- **v2** — the coding-session conductor (the differentiator)
- **v3** — web agent on the owner's real Chrome (consent-gated CDP)
- **v4** — desktop vision + the full ladder

---
Built by [Emmanuel Chukwudebere](https://github.com/Emmanuel-Chukwudebere). Follow the daily build on X.
