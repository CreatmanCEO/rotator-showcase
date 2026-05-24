# Rotator Voice Co-pilot -- design

**Date:** 2026-05-22
**Status:** Design approved. Phase A+B shipped. Phase C-D planned.

---

## 1. Problem

Rotator started as an MCP peripheral for Claude Code (memory + voice passthrough).
The original vision was broader: a voice agent that listens to context (a call,
or just the operator talking), decides when to help, opens files/pages/memory,
and supports conversational correction ("not that, redo it").

After the ADR-2026-05-20 inversion, the old TUI died with the legacy orchestrator.
Claude Code in the terminal doesn't know what the operator says aloud and has no
overlay.

## 2. Solution overview

**Voice Co-pilot** -- a new module `src/rotator/call/` that:

1. Listens to the operator's microphone (Phase A-E, solo mode)
2. Transcribes via Deepgram streaming
3. On each final utterance, dispatches to Claude Code via `call_consult` MCP tool
4. Claude responds with structured JSON: bubble, citations, suggested actions
5. Surface: Textual TUI (Phase A-B), then PyQt6 overlay (Phase C)

## 3. Architecture

```
Windows (operator's PC):
  rotator call
    +-- audio_capture.py    mic -> PCM chunks
    +-- stt_stream.py       Deepgram nova-3 streaming, partials+finals
    +-- transcript.py       rolling buffer + ConversationState
    +-- orchestrator.py     stateful loop -- dispatches to MCP
    +-- prompts/
        +-- system_prompt.py    5-layer composable prompt

       | HTTP+SSE via Tailscale
       v
VPS: rotator-mcp.service
  + MCP tool: call_consult(state: ConversationState) -> ClaudeResponse

       | subprocess
       v
Claude Code (Max 5x subscription)
  reads state -> dispatches its own MCP tools in parallel:
    mnemo_recall, mnemo_event_content, Read, Grep,
    chrome-devtools.navigate_page, session_note
  returns structured JSON: {bubble, citations, suggested_actions, confidence}
```

This reuses the existing MCP stack (rotator-mcp.service with Bearer auth, MNEMO,
all existing tools). All tools (Read, Grep, chrome-devtools, mnemo_recall) are
available to Claude inside call_consult without separate integration.
Matches ADR-2026-05-20 (Claude Code = brain).

## 4. Conversation state + intent classification

ConversationState lives in the overlay process, survives hotkey presses,
is read by Claude each time:

```python
@dataclass
class ConversationState:
    session_id: str
    turns: list[Turn]           # rolling window, last ~30
    current_task: Task | None
    pending_clarification: str | None
    mode: Literal["solo", "call"]

@dataclass
class Turn:
    speaker: Literal["me", "them", "system"]
    intent: Intent | None
    text: str
    timestamp: float
    claude_response: ClaudeResponse | None
```

### Intents (6, exhaustive for MVP)

| Intent | Trigger | Claude action |
|---|---|---|
| `question_to_me` | (Phase F) someone asks the operator a question | <=4 sentences of answer |
| `command` | "open X", "find Y", "write Z" | execute via tools, report briefly |
| `options` | "suggest options", "what's better -- A or B" | 2-3 options + recommendation |
| `correction` | "not that", "you misunderstood", "redo" | re-read previous turn, reformulate, retry if confidence >0.7 |
| `clarification_response` | operator answers Claude's question | continue interrupted task |
| `narration_query` | "what are you doing" during long task | short status, <=2 sentences |

### Correction loop

When intent=correction, Claude does NOT retry silently. First re-reads the
previous response, formulates understanding ("you wanted X, I did Y, so you
actually need Z"), and only retries if confidence >0.7. Otherwise asks.

## 5. Prompt structure (5 layers)

Composed from 5 functions in `prompts/system_prompt.py`:

1. **`role()`** -- who you are, who Nick is, what mode, response goal
2. **`tools()`** -- MCP tool list with usage examples, full project map
3. **`anti_patterns()`** -- what NEVER (fabricate file names, respond without
   tool calls, markdown headings, >4 sentences) and what ALWAYS (cite sources,
   parallel tool calls, ask on ambiguity)
4. **`output_format()`** -- strict JSON: `{type, bubble, citations, suggested_actions, confidence}`
5. **`runtime_context(state)`** -- mode, session duration, last 90s transcript

## 6. Phases

| Phase | Content | Days |
|---|---|---|
| **A** | mic capture + STT + Textual with rolling transcript | 1 |
| **B** | `call_consult` MCP tool + auto-trigger + bubble panel | 2 |
| **C** | PyQt6 overlay always-on-top, transparent, replaces Textual | 2 |
| **D** | intent classifier + correction/clarify/narration intents | 3 |
| **E** | Legacy cleanup + eval fixtures + docs | 1 |
| **F** (later) | WASAPI loopback + diarization for live calls | 2 |

Phase A+B shipped 2026-05-22. Phase C-F planned.

## 7. Tech stack

| Component | Choice | Why |
|---|---|---|
| Mic capture | `sounddevice` | Already in use |
| STT | Deepgram `nova-3` streaming | ru+en, already integrated |
| TUI (Phase A-B) | Textual | Python, fast prototyping |
| Overlay (Phase C) | PyQt6 | Transparency, click-through, always-on-top |
| Brain | Claude Code subprocess via `call_consult` | Max 5x = included; existing tools available |
| State persistence | JSON + markdown in `~/.rotator/calls/` | Single-tenant |
