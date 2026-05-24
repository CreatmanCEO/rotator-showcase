# rotator-showcase

This is a **public showcase repo** -- curated excerpts from the private
`rotator` repository. The full source is at
[github.com/CreatmanCEO/rotator](https://github.com/CreatmanCEO/rotator)
(access on request).

## Navigation

- `README.md` -- project overview, architecture, key decisions
- `ARCHITECTURE.md` -- Mermaid diagrams (topology, MCP internals, voice flow, telemetry)
- `CHANGELOG.md` -- curated build trajectory
- `docs/` -- ADR and design documents (sanitized)
- `samples/` -- 7 Python source files for code review (not runnable standalone)
- `LICENSE` -- MIT
- `.env.example` -- all env vars, zero real values

## What is NOT here

- Tests (not runnable without the full stack)
- Legacy orchestrator/squads code (deprecated)
- Voice driver-level code (mic, STT, TTS internals)
- Real credentials or private infrastructure details

## Source repo

The private repo has 76 Python modules, 262 tests, full CI, and deployment
docs. This showcase contains the most architecturally interesting parts.
