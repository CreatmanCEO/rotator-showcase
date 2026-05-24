# Changelog (curated highlights)

Full changelog lives in the private repo. This is a curated view showing
the build trajectory -- grouped by feature area, not by date.

## MCP Server

- 10 tools exposed via FastMCP (stdio + streamable-HTTP transport)
- Bearer auth middleware wrapping FastMCP's ASGI app for remote access
- DNS-rebinding protection via `ROTATOR_MCP_ALLOWED_HOSTS`
- Deployed as `rotator-mcp.service` (systemd) on VPS, Tailscale-fronted
- Multi-host wiring: Windows, VPS shell, iPhone-via-SSH -- all share the
  same MCP endpoint

## Voice Co-pilot

- **Phase A** -- `rotator call --device N`: mic capture via sounddevice,
  Deepgram nova-3 streaming STT (ru+en), `TranscriptBuffer` with rolling
  state + markdown persistence to `~/.rotator/calls/<session>.md`
- **Phase B** -- `call_consult` MCP tool: dispatches every final utterance
  to Claude Code subprocess with a 5-layer composable system prompt.
  Textual TUI gains bubble panel (yellow border) for structured responses.
  `ConversationState` with rolling window of 30 turns.
- Shared brain dispatch (`call/consult.py`) eliminates code duplication
  between local orchestrator and MCP tool paths

## Telemetry

- **M1** -- `@with_telemetry` decorator wraps all 10 MCP tools. Records
  tool name, latency_ms, args_hash, timestamp to SQLite. Fail-soft: never
  lets observability break the system being observed.
- **M2** -- `rotator_self_report` MCP tool: Claude self-reports per-tool
  verdicts (`helpful`/`neutral`/`waste`) at session end. `waste` signal
  is the primary feedback for prompt improvement.
- `rotator usefulness today` CLI: per-tool call counts, avg/p95 latency,
  self-report verdicts

## Memory + Chronicles

- MNEMO client with TTL cache, async httpx, 9 evidence URI schemes
- Hierarchical chronicle builder (daily/weekly/monthly) -- reads events
  from MNEMO, synthesizes via LLM, posts structured chronicle back
- Session notes: append-only markdown per conversation

## Foundation (MVP week)

- Architecture docs, SPEC, Q1-Q10 decisions, IMPLEMENTATION_PLAN
- 6-layer prompt engine (legacy orchestrator path, now deprecated)
- Bounded loop with citation guards and PII redaction
- Skill extraction + evaluation pipeline with MNEMO sync
- YAML fixture-based eval runner
- Python package skeleton (hatchling, ruff, pytest, mypy)
- GitHub Actions CI (ruff + pytest + no-openai grep)

## Numbers at ship

- 262 tests passing, ruff clean
- ~3,300 LOC across 76 Python modules
- 14,000+ MNEMO events
- Built in 5 days (MVP) + 3 days (voice + telemetry)
