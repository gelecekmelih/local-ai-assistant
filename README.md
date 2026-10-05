# Klargus — Local AI Desktop Assistant (Agent)

Klargus is a **local, offline-first AI assistant/agent for Windows** — a
JARVIS-style system that understands Turkish voice and text, reads and interprets
the screen, and controls the PC (keyboard, mouse, apps). It orchestrates a
**local LLM** (via [Ollama](https://ollama.com)) with a large set of tools,
rather than calling any cloud API.

> ⚠️ **Work in progress & self-directed personal project** (not client work).
> It's a learning project I build to go deep on LLM agents, local inference and
> reliable tool-calling. Model weights and personal data are intentionally **not**
> included in this repository (see *Setup*).

## Highlights (engineering)

- **Agent core with ~140 tool functions** orchestrated by a single execution
  path (`SovereignBrain`) — the LLM plans, picks tools, and self-summarizes.
- **Structured `ToolResult` architecture** — every tool returns a typed result
  object instead of raw strings, which removes fragile keyword-matching and makes
  tool output predictable to parse and test.
- **~200 characterization tests** covering the LLM-visible tools, the security
  layer and the result normalizer.
- **Security boundaries baked in** — input sanitization (e.g. `shlex.quote` on
  shell paths), guarded self-modifying actions, and a log redactor that strips
  passwords/tokens/API keys from logs.
- **Runs offline on limited hardware** (designed around an 8 GB-VRAM budget):
  local LLM, local STT/TTS, local vision.

## What it can do

- **Voice & text control** — Turkish wake-word, STT (faster-whisper) and
  streaming TTS (Piper).
- **Screen understanding** — reads and interprets the active window via UI
  Automation + OCR ("what's on screen / what does it say").
- **PC control** — open apps/files/sites, type, click, scroll, keyboard shortcuts.
- **Memory & learning** — per-user profile, fact extraction and a knowledge base
  (SQLite FTS + embeddings) so it adapts over time.
- **Defensive security assistant** — lists processes/connections and flags
  suspicious indicators; read-only by design (never kills a process or changes
  system settings without an explicit approval flow).
- **Security/network tooling** — thin wrappers around standard, publicly
  available tools (nmap, dns/whois/ssl checks, hashing) for a security student's
  learning workflow.

## Architecture

```text
voice / text command
  → user model & feedback learning
  → safety gate
  → fast local planner            (handles simple actions without the LLM)
  → local LLM planner (Ollama)    (when the task needs reasoning / tools)
  → tools: screen / window / mouse / keyboard / apps / web / system / security
  → structured ToolResult → normalizer
  → memory update
```

## Tech stack

Python · Ollama (local LLM) · faster-whisper (STT) · Piper (TTS) ·
OpenCV / YOLOv8 (vision) · SQLite + embeddings (memory) · Tkinter (HUD/UI).

## Project structure

```text
brain/        LLM orchestration, memory, tool registry, planners, self-repair
tools/        the tool implementations (system, web, file, vision, security, …)
vision/       screen reading & interpretation, input actions
audio/        wake-word, STT, TTS, VAD, device management
control/      command routing & program index
core/         logger (with secret redaction), startup, safe-mode, tracing
security/     defensive security monitor
ui/           Tkinter HUD / settings / tray
tests/        ~200 characterization & unit tests
docs/         architecture notes
```

## Setup

> Model weights (voice/vision), the local LLM and all personal data are **not**
> shipped in this repo. You bring your own.

```bat
:: 1) Python deps (reproducible lock, or the readable list)
pip install -r requirements.lock

:: 2) A local LLM via Ollama + the voice/vision model files (see docs/)

:: 3) Start
start_klargus.bat
```

## Status

Actively developed. Some subsystems are mid-refactor and a few commands are
marked as planned in the code. Built and maintained solo.
