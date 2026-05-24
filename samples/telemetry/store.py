# Excerpt from rotator (private repo). Full source: github.com/CreatmanCEO/rotator (access on request)
# This file is provided for code review purposes — it runs in context of the full project.

"""SQLite store for rotator telemetry.

Schema versioning via `_schema_version` table — drop/recreate on
incompatible bumps (single-tenant, no migration cost).

See docs/plans/2026-05-23-rotator-metrics-design.md section 3.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1


@dataclass
class ToolCall:
    ts: float
    source: str                       # 'server-middleware' | 'client-hook'
    client_host: Optional[str]
    session_id: Optional[str]
    tool_name: str
    args_hash: str
    latency_ms: int
    result_size_bytes: int
    result_truncated: bool
    error: Optional[str]


@dataclass
class ToolAggregate:
    tool_name: str
    calls_total: int
    errors_total: int
    avg_latency_ms: float
    p95_latency_ms: float


@dataclass
class SelfReport:
    ts: float
    session_id: str
    per_tool_verdict: dict[str, str]  # {"mnemo_recall": "helpful", ...}
    notes: str = ""
    inferred_phase: Optional[str] = None  # impl/debug/polish/deploy/None
    project: Optional[str] = None


class TelemetryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init(self) -> None:
        c = self._connect()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS _schema_version (
                version INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY,
                ts REAL NOT NULL,
                source TEXT NOT NULL,
                client_host TEXT,
                session_id TEXT,
                tool_name TEXT NOT NULL,
                args_hash TEXT,
                latency_ms INTEGER,
                result_size_bytes INTEGER,
                result_truncated INTEGER,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tool_calls_ts ON tool_calls(ts);
            CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                started_at REAL,
                ended_at REAL,
                client_host TEXT,
                project TEXT,
                phase TEXT,
                total_tool_calls INTEGER DEFAULT 0,
                total_tokens_in INTEGER DEFAULT 0,
                total_tokens_out INTEGER DEFAULT 0,
                total_cost_usd REAL DEFAULT 0.0
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);

            CREATE TABLE IF NOT EXISTS self_reports (
                id INTEGER PRIMARY KEY,
                session_id TEXT,
                ts REAL,
                per_tool_verdict TEXT,
                notes TEXT,
                inferred_phase TEXT,
                project TEXT
            );
            """
        )
        row = c.execute("SELECT version FROM _schema_version LIMIT 1").fetchone()
        if row is None:
            c.execute("INSERT INTO _schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    def insert_tool_call(self, tc: ToolCall) -> int:
        c = self._connect()
        cur = c.execute(
            """
            INSERT INTO tool_calls (
                ts, source, client_host, session_id, tool_name,
                args_hash, latency_ms, result_size_bytes,
                result_truncated, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tc.ts, tc.source, tc.client_host, tc.session_id, tc.tool_name,
                tc.args_hash, tc.latency_ms, tc.result_size_bytes,
                1 if tc.result_truncated else 0, tc.error,
            ),
        )
        return cur.lastrowid

    def list_tool_calls(self, limit: int = 100) -> list[ToolCall]:
        c = self._connect()
        rows = c.execute(
            "SELECT * FROM tool_calls ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            ToolCall(
                ts=r["ts"], source=r["source"], client_host=r["client_host"],
                session_id=r["session_id"], tool_name=r["tool_name"],
                args_hash=r["args_hash"], latency_ms=r["latency_ms"],
                result_size_bytes=r["result_size_bytes"],
                result_truncated=bool(r["result_truncated"]),
                error=r["error"],
            )
            for r in rows
        ]

    def count_tool_calls(self) -> int:
        c = self._connect()
        return c.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]

    def insert_self_report(self, sr: SelfReport) -> int:
        import json
        c = self._connect()
        cur = c.execute(
            """
            INSERT INTO self_reports (ts, session_id, per_tool_verdict, notes, inferred_phase, project)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                sr.ts,
                sr.session_id,
                json.dumps(sr.per_tool_verdict, ensure_ascii=False),
                sr.notes,
                sr.inferred_phase,
                sr.project,
            ),
        )
        return cur.lastrowid

    def list_self_reports(self, limit: int = 100) -> list[SelfReport]:
        import json
        c = self._connect()
        rows = c.execute(
            "SELECT * FROM self_reports ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        out: list[SelfReport] = []
        for r in rows:
            try:
                verdict = json.loads(r["per_tool_verdict"]) if r["per_tool_verdict"] else {}
            except (TypeError, ValueError):
                verdict = {}
            out.append(
                SelfReport(
                    ts=r["ts"],
                    session_id=r["session_id"],
                    per_tool_verdict=verdict,
                    notes=r["notes"] or "",
                    inferred_phase=r["inferred_phase"],
                    project=r["project"] if "project" in r.keys() else None,
                )
            )
        return out

    def count_sessions(self) -> int:
        c = self._connect()
        return c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _day_bounds(day: Optional[str]) -> tuple[float, float]:
    """Return (start_ts, end_ts) for the given YYYY-MM-DD day, or today."""
    if day is None:
        d = dt.date.today()
    else:
        d = dt.date.fromisoformat(day)
    start = dt.datetime.combine(d, dt.time.min).timestamp()
    end = start + 86400.0
    return start, end


def daily_tool_aggregates(
    store: TelemetryStore, day: Optional[str] = None
) -> list[ToolAggregate]:
    """Per-tool aggregates over a single day. day=None -> today (local TZ)."""
    start, end = _day_bounds(day)
    c = store._connect()
    rows = c.execute(
        """
        SELECT tool_name,
               COUNT(*) AS calls_total,
               SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors_total,
               AVG(latency_ms) AS avg_latency_ms
        FROM tool_calls
        WHERE ts >= ? AND ts < ?
        GROUP BY tool_name
        ORDER BY calls_total DESC
        """,
        (start, end),
    ).fetchall()

    # SQLite has no PERCENTILE_CONT; compute p95 in Python.
    aggs: list[ToolAggregate] = []
    for r in rows:
        latencies = [
            row[0]
            for row in c.execute(
                "SELECT latency_ms FROM tool_calls "
                "WHERE tool_name = ? AND ts >= ? AND ts < ? "
                "ORDER BY latency_ms",
                (r["tool_name"], start, end),
            ).fetchall()
        ]
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
        aggs.append(
            ToolAggregate(
                tool_name=r["tool_name"],
                calls_total=r["calls_total"],
                errors_total=r["errors_total"] or 0,
                avg_latency_ms=float(r["avg_latency_ms"] or 0.0),
                p95_latency_ms=float(p95),
            )
        )
    return aggs


def daily_self_reports(
    store: TelemetryStore, day: Optional[str] = None
) -> list[SelfReport]:
    """Self-reports filed within a single day. day=None -> today (local TZ)."""
    import json
    start, end = _day_bounds(day)
    c = store._connect()
    rows = c.execute(
        "SELECT * FROM self_reports WHERE ts >= ? AND ts < ? ORDER BY ts",
        (start, end),
    ).fetchall()
    out: list[SelfReport] = []
    for r in rows:
        try:
            verdict = json.loads(r["per_tool_verdict"]) if r["per_tool_verdict"] else {}
        except (TypeError, ValueError):
            verdict = {}
        out.append(
            SelfReport(
                ts=r["ts"],
                session_id=r["session_id"],
                per_tool_verdict=verdict,
                notes=r["notes"] or "",
                inferred_phase=r["inferred_phase"],
                project=r["project"] if "project" in r.keys() else None,
            )
        )
    return out


def daily_verdict_tally(
    store: TelemetryStore, day: Optional[str] = None
) -> dict[str, dict[str, int]]:
    """For each tool, count how many self-reports tagged it helpful/neutral/waste.

    Returns: {tool_name: {verdict: count, ...}, ...}
    """
    tally: dict[str, dict[str, int]] = {}
    for sr in daily_self_reports(store, day=day):
        for tool, verdict in sr.per_tool_verdict.items():
            tally.setdefault(tool, {})[verdict] = tally.setdefault(tool, {}).get(verdict, 0) + 1
    return tally


def default_store_path() -> Path:
    """Path resolution: ROTATOR_TELEMETRY_DB env, then /opt/rotator/telemetry.db
    (VPS), then ~/.rotator/telemetry.db (Windows / local dev)."""
    import os
    env = os.environ.get("ROTATOR_TELEMETRY_DB")
    if env:
        return Path(env)
    opt = Path("/opt/rotator/telemetry.db")
    if opt.parent.is_dir():
        return opt
    return Path.home() / ".rotator" / "telemetry.db"
