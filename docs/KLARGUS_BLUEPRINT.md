# Klargus Blueprint

Klargus is being shaped as a local JARVIS-like assistant, but with real-world
software boundaries and safety controls. The system name is Klargus.

## Core Layers

1. Voice layer
   - Wake word listening.
   - Command speech-to-text.
   - Local Turkish TTS.
   - Listening calibration.
   - User voice profile foundation.

2. Perception layer
   - Active window detection.
   - UI Automation tree reading.
   - OCR-based screen text reading.
   - Screen summaries for reasoning.

3. Memory layer
   - Profile and preferences.
   - Facts, events and recent interactions.
   - Learned command aliases.
   - Personalization context for Ollama.

4. Decision layer
   - Deterministic command parsing for reliable simple actions.
   - Ollama planner for complex or multi-step goals.
   - Central `KlargusKernel` for capability status, planning, safety and tool execution.

5. Action layer
   - Open apps and URLs.
   - Bring already-running apps to the foreground.
   - Click, double-click, right-click, scroll, type and hotkeys.
   - Retry/continue recent UI actions.

6. Safety layer
   - Risk filtering for destructive/system commands.
   - Permission architecture for sensitive actions.
   - No autonomous harmful, destructive, weaponized or unauthorized actions.

7. Physical-world adapter layer
   - Adapter registry for Home Assistant, MQTT, cameras, robots or other local services.
   - Real device control stays inactive until a real endpoint/token/service is configured.

## Current Truth

Klargus is not a Marvel-level AI. It is now a modular local assistant core that
can grow toward that direction:

- Works locally where possible.
- Uses Ollama for planning and language reasoning.
- Uses local screen/OCR and keyboard/mouse tools for computer control.
- Keeps memory and personalization on disk.
- Has a safe adapter boundary for future physical devices.

## Next Engineering Milestones

1. Replace command STT with Whisper/whisper.cpp for stronger Turkish speech recognition.
2. Add visual model support for deeper screen understanding beyond OCR/UIA.
3. Expand the adapter layer with the user's actual devices and APIs.
4. Add plan verification after every action using screen diffs and OCR.
5. Add a long-running goal manager for background tasks and reminders.
6. Add stronger voice authentication after enough user voice samples.
