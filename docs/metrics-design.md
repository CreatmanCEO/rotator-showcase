# Rotator Usefulness Metrics + Time Accounting -- design

**Date:** 2026-05-23
**Status:** M1+M2 shipped. M3-M5 planned.

---

## 1. Goal (twofold)

### Goal A -- tool usefulness (informs 3 decisions)
1. **ROI:** worth continuing to invest in rotator infrastructure?
2. **Where to improve:** which tools underused, which give false hits, which are slow?
3. **Feedback loop:** rotator sees Claude ignored mnemo_recall -- knows the prompt
   or ranking needs fixing.

### Goal B -- project time accounting (forward-looking estimation)
Track how long projects/tasks physically take, decomposed by phase
(implementation / debug / polish / deploy). Future-Claude querying metrics gets
honest estimation basis for new projects.

Explicitly rejected as goal: resume cosmetic narrative. If the metrics produce
a good story, fine; primary purpose is decisions.

## 2. Architecture

```
+----------------------------------+
| Client (Windows / VPS / iPhone)  |
|  Claude Code session             |
|   -> tool calls (rotator + mnemo)| --+  HTTP
|                                  |   |
|   rotator_self_report MCP tool --+-->|  VPS: rotator-mcp.service
|   (Claude calls at end)          |   |
+----------------------------------+   |   middleware times every tool,
                                       |   writes telemetry.db
                                       |
                                       |   /opt/rotator/telemetry.db
                                       |    tool_calls
                                       |    sessions
                                       |    self_reports
                                       |    project_sessions
                                       |
                                       |   cron 04:35 (after chronicle)
                                       |    -> daily aggregator -> MNEMO event
                                       |
                                       |   weekly self-eval task
                                       |    -> low-util tools highlighted
                                       |    -> prompt-patch suggestions
                                       +-----------------------------------
```

## 3. Storage schema (SQLite)

Single-tenant; SQLite is sufficient, no Postgres overhead.

```sql
CREATE TABLE tool_calls (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  source TEXT NOT NULL,                -- 'server-middleware' | 'client-hook'
  client_host TEXT,
  session_id TEXT,
  tool_name TEXT NOT NULL,
  args_hash TEXT,                      -- sha256, NOT raw args (privacy)
  latency_ms INTEGER,
  result_size_bytes INTEGER,
  result_truncated BOOLEAN,
  error TEXT
);

CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  started_at REAL,
  ended_at REAL,
  client_host TEXT,
  project TEXT,
  phase TEXT,                          -- impl | debug | polish | deploy
  total_tool_calls INTEGER,
  total_tokens_in INTEGER,
  total_tokens_out INTEGER,
  total_cost_usd REAL
);

CREATE TABLE self_reports (
  id INTEGER PRIMARY KEY,
  session_id TEXT REFERENCES sessions(session_id),
  ts REAL,
  per_tool_verdict TEXT,               -- JSON: {"mnemo_recall": "helpful", ...}
  notes TEXT,
  inferred_phase TEXT
);

CREATE TABLE project_sessions (
  project TEXT,
  phase TEXT,
  date TEXT,
  total_minutes REAL,
  session_count INTEGER,
  PRIMARY KEY (project, phase, date)
);
```

Privacy: `args_hash` not raw args. Tailscale-fronted DB; no cross-tenant.

## 4. Components

### 4.1 Server-side middleware (M1 -- shipped)
`@with_telemetry` decorator wraps FastMCP tool dispatch. Records ts, tool_name,
args_hash, latency_ms, result_size_bytes, error. Single file:
`src/rotator/telemetry/middleware.py`. Fail-soft -- never lets observability
break the system being observed.

### 4.2 `rotator_self_report` MCP tool (M2 -- shipped)
Claude calls near end of session to log perceived utility. Per-tool verdicts
(`helpful`/`neutral`/`waste`), free-form notes, inferred phase, project.
Multiple per-session allowed for long sessions.

### 4.3 Client hooks (M3 -- planned)
`~/.claude/hooks/post-tool-use` captures non-rotator-mcp tools (Read, Grep,
chrome-devtools, etc.) that server middleware never sees. Syncs to VPS on
session end.

### 4.4 Daily aggregator cron (M4 -- planned)
Cron at 04:35 UTC: reads yesterday's data, computes per-tool aggregates (calls,
avg_latency, error_rate, verdict rates), writes structured MNEMO event
`rotator-usefulness-YYYY-MM-DD`.

### 4.5 Weekly self-eval (M5 -- planned)
Sunday cron: Claude Code task on VPS audits the week's usefulness data. Identifies
tools with >30% waste verdicts, suggests prompt patches. Writes MNEMO event
`rotator-weekly-eval-YYYY-Www`. This closes the feedback loop: rotator-as-system
improves rotator-as-prompts.

## 5. CLI

```
rotator usefulness today
rotator usefulness week
rotator usefulness tool mnemo_recall
rotator estimate "build Telegram bot with payments"
```

`estimate` queries `project_sessions` for projects with similar description,
reports observed time distribution by phase.

## 6. Acceptance criteria

- **M1 done:** `rotator usefulness today` shows >=3 tools with counters after
  a day of work. Latency p50/p95 visible.
- **M2 done:** at least one self_report in DB from a Claude session ended naturally.
- **M3 done:** Windows hook records non-rotator-mcp tools, sync POST returns 200.
- **M4 done:** MNEMO event `rotator-usefulness-YYYY-MM-DD` exists after cron.
- **M5 done:** MNEMO event `rotator-weekly-eval-YYYY-Www` with specific
  prompt-patch suggestions.
