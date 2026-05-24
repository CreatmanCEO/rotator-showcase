# ADR: Claude Code is the brain, Rotator is its peripheral

**Date:** 2026-05-20
**Status:** Accepted
**Supersedes:** original orchestrator architecture

## Context

After 6 days of building Rotator as a classifier+squads orchestrator that
spawns Claude Code as a subprocess for "execute_code" tasks, the operator
diagnosed the design as backwards. Direct observations:

The current orchestrator:
- routes each utterance through Haiku classifier to ONE narrow squad
- squads are single-shot (BrowserSquad only opens URLs, doesn't read them;
  ResearcherSquad only recalls, doesn't act; ExecutorSquad spawns Claude
  but with a sanitised mini-prompt that strips conversation context)
- no multi-step tool composition: "open URL, read content,
  compose answer, save file" is 4 squads in serial that don't talk
- Claude Code already has all of this natively (Read/Bash/Edit/Grep/MCP/
  planning/conversation memory), and we've been hiding it behind our
  worse orchestrator.

## Decision

Invert the relationship. **Claude Code is the agent. Rotator is its
peripheral.**

```
OLD                              NEW
---                              ---
rotator (orchestrator)           claude code  (brain)
  | spawn                          | uses MCP tools
claude code (slave subprocess)   rotator-mcp-server (peripheral)
                                   - voice_listen()  -> next mic utterance
                                   - voice_speak(text) -> TTS
                                   - mnemo_recall(q)  -> MNEMO recall
                                   - mnemo_write(ev)  -> MNEMO event
                                   - chronicle_build(level, period)
                                   - skill_lookup(name)
                                   - session_note(text)
```

User says something (voice or text) -> Claude Code receives it as a normal
message -> Claude Code plans + uses rotator's MCP tools as needed (voice
output, persistent memory) -> Claude Code uses its built-in tools
(Read/Bash/Edit/chrome-devtools MCP) for everything else.

## Why this is better

1. **Claude Code does what it does best.** It already plans, composes
   tools, handles multi-step intents, manages conversation memory,
   reads files, runs shell, drives browsers. We were re-implementing
   1/10th of this badly.
2. **No classifier needed.** Claude Code IS the classifier -- it decides
   on its own whether the request needs file reading, recall, or just
   a conversational ack.
3. **MCP composition is native.** Claude Code already orchestrates
   multiple MCP servers (chrome-devtools, mnemo MCP, etc.). Rotator-as-
   MCP-server slots in cleanly alongside.
4. **Memory persistence becomes simple.** rotator-mcp-server's
   `mnemo_recall(query)` returns events. Claude Code injects them
   into its own context. The orchestration layer that was "pre-fetching
   recall_blocks for the LLM" disappears.
5. **Voice as another channel.** `voice_listen()` blocks until next mic
   utterance arrives. `voice_speak(text)` flushes through TTS. Claude
   Code uses them the same way it uses Bash -- just another tool.
6. **Identical experience across hosts.** Whether operator runs `claude`
   on Windows, VPS, iPhone-via-ssh, or via a Mobile Claude session,
   the same MCP server provides voice + memory.

## What dies / what stays

**Dies (or moves to opt-in legacy mode):**
- `task_classifier.py` -- Claude Code does this internally.
- All `squads/*.py` -- replaced by direct MCP calls from Claude Code.
- `orchestrator/loop.py` -- Claude Code's own loop replaces it.
- `tui/app.py` voice-loop wiring -- repurposed as a wrapper around a
  Claude Code session.
- The "trajectory" abstraction with budgets and citation guards stays
  for cron jobs (legacy mode) but is no longer the primary path.

**Stays:**
- `memory/mnemo_client.py` -- wrapped by MCP tools.
- `voice/microphone.py`, `voice/stt.py`, `voice/tts.py`,
  `voice/session.py` (smart-EoU) -- wrapped by MCP tools.
- `chronicler.py` -- wrapped as MCP tool `chronicle_build()`.
- `skills/extractor.py` and `eval/runner.py` -- wrapped as MCP tools.
- MNEMO itself (VPS service) -- untouched.

## Migration path

**Phase 1** -- rotator-mcp-server scaffold with 6-8 tools (read-only or cheap-write).
**Phase 2** -- voice CLI replacement: Claude Code subprocess with MCP pre-loaded.
**Phase 3** -- multi-host auth (Max 5x subscription on VPS).
**Phase 4** -- retire orchestrator, keep functional for cron-only paths.

## Bottom line

Stop pretending we have an agent. Use Claude Code as the agent. Give
it a voice and a long-term memory through MCP, and step out of the way.
