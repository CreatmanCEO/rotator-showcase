# Rotator -- Architecture

> Curated diagrams from the private repo. Full architecture doc has 5 Mermaid
> diagrams and transport/storage tables; this file shows the 4 most relevant.

## High-level topology

How operator devices connect to the rotator MCP server and MNEMO backend.
All traffic flows over Tailscale; no public internet exposure.

```mermaid
graph TB
    subgraph "Operator devices"
        WIN["Windows PC<br/>Claude Code + rotator call"]
        IPHONE["iPhone<br/>Termius SSH"]
        SW4CLI["sw4 shell<br/>Claude Code"]
    end

    subgraph "sw4 VPS"
        MCP["rotator-mcp.service<br/>port 18081<br/>10 MCP tools"]
        MNEMO["MNEMO API<br/>port 18080<br/>FastAPI"]
        PG["Postgres + pgvector<br/>14k+ events"]
        TEL["telemetry.db<br/>SQLite"]
    end

    WIN -->|"HTTP+SSE<br/>Bearer auth<br/>Tailscale"| MCP
    IPHONE -->|"SSH -> claude"| SW4CLI
    SW4CLI -->|"HTTP loopback"| MCP
    MCP -->|"httpx"| MNEMO
    MNEMO -->|"asyncpg"| PG
    MCP -->|"SQLite"| TEL
```

## MCP server internals

The server wraps FastMCP with Bearer auth middleware and a telemetry decorator
that records every tool call to SQLite before the result propagates.

```mermaid
graph LR
    subgraph "mcp_server.py"
        FASTMCP["FastMCP app<br/>stdio | streamable-http"]
        AUTH["Bearer middleware<br/>(HTTP only)"]
        TM["telemetry decorator<br/>with_telemetry()"]
    end

    subgraph "10 MCP tools"
        R["mnemo_recall"]
        E["mnemo_events"]
        EC["mnemo_event_content"]
        W["mnemo_write"]
        S["mnemo_state"]
        CB["chronicle_build"]
        SN["session_note"]
        CC["call_consult"]
        SR["rotator_self_report"]
        SL["skill_lookup"]
    end

    FASTMCP --> AUTH
    AUTH --> TM
    TM --> R & E & EC & W & S & CB & SN & CC & SR & SL
```

## Voice co-pilot data flow

The `rotator call` command captures mic audio, streams to Deepgram STT,
buffers transcript, and dispatches final utterances to Claude Code subprocess
for structured response.

```mermaid
sequenceDiagram
    participant Mic as Microphone
    participant STT as DeepgramSTT
    participant TB as TranscriptBuffer
    participant Orch as CallOrchestrator
    participant Claude as Claude Code subprocess
    participant UI as Textual TUI

    Mic->>STT: PCM audio chunks (16kHz mono)
    STT->>TB: TranscriptEvent (interim/final)
    TB->>UI: update transcript panel
    TB->>TB: persist to ~/.rotator/calls/<session>.md
    STT->>Orch: final utterance
    Orch->>Claude: ConversationState (last 90s context)
    Claude->>Orch: JSON {type, bubble, citations, actions, confidence}
    Orch->>UI: render bubble (yellow border)
```

## Telemetry subsystem

Every tool call is timed by the `@with_telemetry` decorator. Claude self-reports
tool usefulness at session end. The `waste` signal is the most valuable --
it drives prompt and routing improvements.

```mermaid
graph TB
    subgraph "Runtime"
        DEC["@with_telemetry decorator"]
        STORE["TelemetryStore (SQLite)"]
    end

    subgraph "Tables"
        TC["tool_calls<br/>tool, latency_ms, args_hash, ts"]
        SESS["sessions<br/>session_id, start/end, tools used"]
        SREP["self_reports<br/>per_tool_verdict, notes, phase"]
    end

    subgraph "CLI"
        CMD["rotator usefulness today"]
    end

    DEC -->|"insert on each call"| STORE
    STORE --> TC & SESS & SREP
    CMD -->|"read + aggregate"| STORE
```
