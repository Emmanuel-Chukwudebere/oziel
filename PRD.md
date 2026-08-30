# Oziel — Product Requirements Document

**Status:** Draft v0.6 · 2026-08-30
**Name:** Oziel — Igbo *ozi* (message/errand) + the angelic *-el*: "the errand angel". Formerly codenamed Jarvis.
**Wake word:** "Oziel" (custom openWakeWord model — trained and validated in M0)
**Owner:** Immanuel

---

## 1. Vision

A voice-first assistant that lives on your computer and works for you like a capable colleague. You talk to it — through the laptop mic or earbuds from another room — and it acts: runs commands, manages files, operates apps and the browser, and above all **conducts your AI coding sessions** (Claude Code) so you can direct software development by voice while away from the keyboard.

The laptop is the ears, mouth, and hands; the brain is Mistral's API (free tier). The laptop stays fast because it hosts no models — and a small offline fallback keeps the basics working with no connection at all.

## 2. Target user

- **v1 audience:** the owner. One developer, one Windows laptop.
- **Later (only if v2 proves itself in daily use):** developers who live in agentic coding tools and want hands-free orchestration. Monetization is deferred until the owner has daily-driven v2 for a month.
- The project is **built in public**: daily progress updates on X. Identity to secure (owner action): domain **oziel.dev** (and/or **oziel.ai**), GitHub org **ozielai** (or `oziel-ai` / `getoziel` / `ozielhq`), X handle (verify `@oziel` / `@ozielai` / `@getoziel` manually — availability confirmed for domains and GitHub on 2026-08-30, X unverifiable programmatically).

## 3. Product principles

1. **Light on the laptop.** No local model hosting. Resident footprint under ~1 GB RAM and near-zero idle CPU. The owner's machine must never feel slower because Oziel exists.
2. **Zero monthly cost.** The brain, STT, and TTS run on Mistral's free plan ($10/month in API credits). M0 verifies a typical day fits the envelope; usage tracking throttles gracefully before credits run out.
3. **Latency is a feature.** Every interaction has an explicit budget (§6). A feature that can't meet its budget doesn't ship.
4. **Deterministic before intelligent.** Cheap, reliable methods always run before model calls (the action ladder, §5.3) — this is now a cost control as much as a speed control.
5. **It learns your machine.** Every solved task becomes a replayable skill. Oziel gets faster (and cheaper) with use.
6. **Consent for consequences.** Risky actions require the owner's go-phrase, set during first-run setup — never a bare "yes", never a shipped default.
7. **Graceful offline degradation.** No internet → Oziel says so once and keeps handling the basics locally (F8). Smart features return when connectivity does.
8. **Provider-swappable.** All model calls go through one internal `ModelProvider` interface. Mistral is the launch provider; swapping providers (or going local someday) is config, not surgery.

## 4. Core features (priority order)

### F1 — Voice loop (v1)
Wake word "Oziel" (local, custom openWakeWord model) → streaming STT (**Voxtral Mini Transcribe Realtime**) → intent routing (**Mistral Medium 3.5**) → action → spoken reply (**Voxtral TTS**, streamed sentence-by-sentence). Works with laptop mic/speakers and Bluetooth earbuds (earbuds become default audio when connected). Proactive speech allowed: Oziel may initiate ("the session finished").
Includes a **voice-driven first-run setup** (decided 2026-08-30): the API key is the *only* typed input (random characters can't be dictated). After the key is pasted, Oziel starts speaking through the system-default output and conducts the rest of setup as a conversation — hearing check, voice/emotion choice, devices. The **go-phrase is captured by voice, not typed**: Oziel transcribes it, reads it back for confirmation, then has the owner repeat it twice as a recognition-reliability check — guaranteeing the phrase that guards risky actions is one STT provably recognizes in the owner's voice. Every spoken question is mirrored on screen as tappable options (voice-first, click-fallback).

### F1b — "The Presence" window (v1)
A single small window (pywebview: native window rendering HTML/CSS, driven directly by the Python process — no Electron, no server), opened from a system-tray icon. A living **orb** is the interface: it breathes when idle, ripples when listening, glows when speaking, dims when offline or paused. Beneath it: live captions of what Oziel heard and said (mishearings visible instantly), a credits meter ($ used vs $10), and a pause-listening toggle. A ⚙ slide-over holds settings: devices, voice + emotion, go-phrase re-record, API key (masked), verbosity, usage. **Every setting the UI can change, voice can also change** ("Oziel, speak faster") — both edit the same config file. Design language matches oziel.vercel.app: dark ink, ivory text, amber accent.

### F2 — Coding-session conductor (v2) · **the differentiator**
Oziel launches and owns coding-agent CLIs (**Claude Code only at launch**) on a pseudo-terminal (ConPTY) it hosts:
- "Read me the summary" → speaks the session's latest output (it already has the raw text; no vision, no scraping).
- Dictated instructions are written to the session's input verbatim after read-back confirmation.
- "Monitor this and alert me" → background watcher; on completion Oziel speaks up unprompted through whatever audio is active.
- Claude Code is a *conducted tool*, not part of Oziel's brain — all of Oziel's own reasoning is Mistral.

**Visibility & take-over (requirement, not optional):** an Oziel-run session is never hidden. Each session renders live in a normal terminal window — everything Oziel types appears as it happens. Because Oziel hosts the ConPTY, the keyboard and Oziel feed the *same* session: the owner can click in and type at any time. Arbitration: **human input always wins** — keystrokes Oziel didn't send put it hands-off on that session ("you've got it") until told "Oziel, take over again". Voice "pause" / "resume" control the same state.

### F3 — Action ladder + confirm gate (v1 rungs 1–2, full ladder by v3/v4)
All actions route through one executor that climbs from cheapest to most expensive method (§5.3). The confirm gate lives in the executor, so every path is covered: actions matching risk rules (send, pay, delete, credential fields, bulk file operations) pause and require the go-phrase.

### F4 — Self-writing skills (v2+)
When Oziel solves a task via exploration (UIA tree or vision), it distills the successful trajectory into a skill file: app name, launch method, step sequence with selectors/coordinates, preconditions. Next time, the skill replays deterministically in seconds — no model calls, no token cost. A failed replay (app updated, UI changed) falls back down the ladder, re-explores, and rewrites the skill. Skills are human-readable files the owner can inspect and edit.

### F5 — Web browsing agent (v3)
DOM/CDP automation on the owner's **real Chrome** (consent-gated remote debugging, Chrome 144+ flow), driven by Mistral Medium 3.5 — real logins and open tabs available, visible cursor. Where the DOM is unhelpful (canvas UIs, weird widgets), fallback is a screenshot to Medium 3.5's built-in vision for grounding. *(Fara was dropped in the v0.4 pivot: it existed to compensate for a weak local text brain; a frontier agentic model reasoning over the DOM removes the need, and nothing needs GPU hosting anywhere.)*

### F6 — Desktop app control (v3/v4)
Rungs 1–4 handle most desktop apps: OS commands, hotkeys, deep links, and the Windows UI Automation tree (pywinauto with `click_input()` for visible real-mouse movement). Where the UIA tree is confused, missing, or wrong, fallback is screenshot → Medium 3.5 vision. Results feed the skill store so each desktop app is only "hard" (and only costs tokens) once.

### F7 — Ambient monitoring & notifications (v2)
Watchers for long-running work (coding sessions first; later: downloads, builds). Alerts are spoken, routed to active audio (earbuds if connected).

### F8 — Offline fallback (v1.5)
A small local pack (~1–2 GB disk) that loads only when connectivity drops: whisper.cpp **tiny/base** for STT plus a **fixed command grammar** (no LLM) covering rungs 1–3 — pause/play music, open/close apps, volume, timers, file operations, "read my last note". Local TTS via Piper. On disconnect Oziel announces once: "we're offline — basics only"; on reconnect, full service resumes silently. Offline never handles risky actions beyond the same confirm gate.

## 5. Architecture

### 5.1 Component stack

**Local (resident, target < 1 GB RAM total):**

| Component | Choice | Notes |
|---|---|---|
| Wake word | openWakeWord, custom "Oziel" model | ~0.1 GB, always on, fully local |
| Audio I/O | mic/speaker/earbud routing, streaming capture | negligible |
| Executor + ladder | Python service (pywinauto, PowerShell, CDP client) | ~0.3 GB |
| Conductor | ConPTY subprocess manager | negligible |
| Skill store | plain files (YAML/JSON) in repo | — |
| Offline pack (F8) | whisper.cpp tiny/base + Piper + command grammar | ~1–2 GB **disk**; loaded into RAM only while offline |

**Cloud (Mistral API, via the `ModelProvider` interface):**

| Role | Model |
|---|---|
| STT | Voxtral Mini Transcribe Realtime (streaming) |
| TTS | Voxtral TTS (streamed) |
| Brain: routing, conversation, agent steps | Mistral Medium 3.5 |
| Vision (rung 5): screenshot grounding | Mistral Medium 3.5 (multimodal) |

### 5.2 Hardware reality (measured 2026-08-29)

i7-8650U (4 cores), 16 GB RAM, Intel UHD 620. Under the thin-client design this is comfortably sufficient: the only always-on compute is wake-word detection and audio streaming. The old constraint (v0.1–v0.3: local model hosting was slow and RAM-hungry) motivated the pivot and no longer applies.

### 5.3 The action ladder

1. **OS commands** (PowerShell): launch, files, settings, volume. Deterministic, instant, free.
2. **Hotkeys / media keys**: play/pause/next, app shortcuts. Deterministic, instant, free.
3. **URI deep links**: `spotify:playlist:…`, `ms-settings:…`. Deterministic, instant, free.
4. **UIA tree** (pywinauto): serialize window controls to text → brain picks action → visible mouse executes. One small model call per step.
5. **Vision**: screenshot → Medium 3.5 grounding. Primary fallback for web canvas and broken UIA trees.

**Skill replay short-circuits the ladder:** a known task never climbs — and never spends tokens.

## 6. Latency & cost budgets

| Interaction | Budget | How it's met |
|---|---|---|
| Wake word → listening | < 0.5 s | local, real-time |
| End of speech → first spoken word (simple query) | ≤ 2 s | streaming STT while user talks; streamed TTS starts on first sentence; budget now dominated by network round-trip — measured in M0 |
| Deterministic action (rungs 1–3) | ≤ 1 s | direct execution, no network |
| UIA step (rung 4) | ≤ 3 s | one small cloud call per step |
| Vision step (rung 5) | 1–3 s | cloud inference |
| Skill replay (whole flow) | ≤ 5 s | no model in the loop |
| Offline fallback command | ≤ 2 s | whisper-tiny + grammar, local |

**Cost envelope:** $10/month free credits. Controls: the ladder and skill store minimize model calls structurally; a local usage meter tracks daily burn; approaching the cap, Oziel warns and degrades gracefully (shorter replies, offline pack for basics) rather than dying mid-month. M0 measures a realistic day's burn before v1 is built.

## 7. Roadmap & acceptance criteria

### M0 — API + wake-word spike (before any product code)
Against the owner's real Mistral key: Voxtral Realtime STT round-trip latency from this laptop's network; Voxtral TTS time-to-first-audio; Medium 3.5 agent-step latency; free-tier rate limits in practice; token/credit burn for a simulated typical day (~100 interactions). Plus: train a custom **"Oziel"** openWakeWord model (synthetic-sample pipeline) and measure detection rate + false triggers over a day of normal room audio; whisper-tiny accuracy on the owner's voice (for F8).
**Exit:** every "est." in §6 replaced by measured numbers; free-tier viability confirmed or the cost model revised; "Oziel" wake word validated.

### v1 — Voice loop + The Presence
Wake word, cloud STT/TTS/brain pipeline, rungs 1–2, confirm gate, earbud routing, voice-driven first-run setup, The Presence window + tray icon (F1b).
**Accept:** "Oziel, pause the music" and "Oziel, create a folder called X and move all PDFs into it" work end-to-end ≤ 2 s to first spoken word; a risky action stalls until the go-phrase; setup completes with only the API key typed; the orb reflects listening/thinking/speaking state live; settings changes from the window and from voice both persist.

### v1.5 — Offline fallback
F8 pack, connectivity watcher, auto-switch both directions.
**Accept:** Wi-Fi off → "pause the music" still works ≤ 2 s; Oziel announced the switch exactly once.

### v2 — Conductor
ConPTY-owned Claude Code sessions; read-aloud, dictate-in with read-back, monitor-and-alert; visible terminal + human-wins take-over.
**Accept:** owner completes one real coding task start-to-finish by voice from another room.

### v3 — Web agent + deep links
Medium 3.5 driving real Chrome via consent-gated CDP; rung 3; skill store v1 (record + replay).
**Accept:** "sort my Spotify playlist" works via the browser; second run replays ≥ 5× faster and cheaper than the first.

### v4 — Desktop vision + full ladder
Rung 4 UIA everywhere, vision fallback, skill self-rewrite on failed replay.
**Accept:** an app with a broken UIA tree still gets operated (via vision first time, skill replay after).

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Mistral free tier shrinks, rate-limits hard, or key is lost | Oziel goes dumb | `ModelProvider` abstraction — swap providers by config; usage meter + graceful degradation; offline pack keeps basics alive |
| Privacy: voice audio and screenshots go to Mistral's servers | data exposure | documented plainly to the owner; offline pack for sensitive moments; risky-action gate is local |
| Network latency variance blows the 2 s voice budget | assistant feels sluggish | streaming at every stage; M0 measures from this laptop's actual connection before commitments |
| STT errors on accented/fast speech → wrong actions | wrong command executed | confirm gate on risky ops; read-back before writing to coding sessions; M0 tests Voxtral on the owner's voice |
| Custom wake word false-triggers or misses | annoyance / distrust | M0 measures over a day of room audio; threshold tuning; fallback to push-to-talk hotkey always available |
| Prompt injection via web pages / session output | agent misled into harmful action | ladder means model output can't act without executor rules; confirm gate; browser attach is per-session consent |
| Chrome/OS updates break automation paths | rungs 3–5 breakage | skills fall back down the ladder and self-rewrite |
| Platform giants ship overlapping features | product squeezed | stay in the lane they won't: dev-centric, conducting *other* agents, user-owned |

## 9. Success metrics (v1–v2, owner-only)

- Owner uses Oziel ≥ 5 days/week after week 2.
- ≥ 80 % of issued tasks resolved by rungs 1–3 or skill replay (no per-step model calls).
- Median end-of-speech → first-word latency ≤ 2 s (rolling 7-day).
- Monthly API burn stays within free credits (hard requirement for v1–v2).
- ≥ 1 real coding task per week completed fully by voice (v2).
- Zero risky actions executed without the go-phrase (hard requirement, not a target).

## 10. Open questions

*(none — all resolved)*

### Resolved
- ~~Public name + wake word~~ — **Oziel** (decided 2026-08-30): Igbo *ozi* (message/errand) + *-el*. Wake word "Oziel". Owner action outstanding: register oziel.dev (and/or .ai), create GitHub org (`ozielai` free at decision time), verify and claim an X handle.
- ~~Local-only hosting~~ — pivoted 2026-08-30 to thin client + Mistral API brain (v0.4). Driver: model hosting would slow and fill the owner's laptop.
- ~~Fara vision model~~ — dropped 2026-08-30; Medium 3.5's DOM reasoning + built-in vision replace it (see F5).
- ~~`claude -p` genius escalation~~ — dropped 2026-08-30; Mistral Medium 3.5 is the brain. Claude Code remains as a conducted tool only.
- ~~LAN GPU box~~ — rejected 2026-08-30 (obsolete after the pivot).
- ~~Go-phrase~~ — owner sets it during first-run setup; no default phrase exists.
- ~~Coding agents at launch~~ — Claude Code only; a second agent considered after v2 is in daily use.
- ~~STT model size~~ — superseded by the pivot: cloud Voxtral Realtime for online STT; whisper.cpp tiny/base for the offline pack (M0 validates both on the owner's voice).
