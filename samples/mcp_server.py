# Excerpt from rotator (private repo). Full source: github.com/CreatmanCEO/rotator (access on request)
# This file is provided for code review purposes — it runs in context of the full project.

"""rotator-mcp — stdio MCP server exposing rotator capabilities to Claude.

ADR 2026-05-20: Claude Code is the brain. This module is the
peripheral that gives Claude long-term MNEMO memory + hierarchical
chronicles + session notes + skill lookup.

Voice tools (voice_listen / voice_speak) live in `mcp_voice_server.py`
and are loaded only when `rotator voice` CLI is used. This server is
safe to wire into `claude_desktop_config.json` on any host.

Tools exposed:
  mnemo_recall(query, project?, limit?)      -> list of events
  mnemo_events(project?, since?, until?, ...) -> temporal listing
  mnemo_write(event)                          -> POST /events
  mnemo_state()                               -> MNEMO health/state JSON
  chronicle_build(level, period?, project?)   -> build + POST chronicle
  session_note(body)                          -> append to ~/.rotator/sessions/mcp-<date>.md
  skill_lookup(query?)                        -> list skills matching
  skill_record(skill)                         -> register a new skill

Run standalone:
  python -m rotator.mcp_server

Wire into Claude Code (claude_desktop_config.json):
  {
    "mcpServers": {
      "rotator": {
        "command": "python",
        "args": ["-m", "rotator.mcp_server"],
        "env": {
          "MNEMO_BASE_URL": "http://localhost:8080",
          "MNEMO_TOKEN": "..."
        }
      }
    }
  }
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from rotator.telemetry.middleware import with_telemetry
from rotator.telemetry.store import TelemetryStore, default_store_path

# ---- shared state ----

_mnemo_url: str | None = None
_mnemo_token: str | None = None
_http: httpx.AsyncClient | None = None
_telemetry_store: TelemetryStore | None = None


def _get_telemetry() -> TelemetryStore:
    """Lazy-init telemetry store on first tool call (avoids startup blocking
    if /opt/rotator isn't writable for some reason)."""
    global _telemetry_store
    if _telemetry_store is None:
        _telemetry_store = TelemetryStore(default_store_path())
        try:
            _telemetry_store.init()
        except Exception:  # noqa: BLE001 — telemetry must never break the server
            pass
    return _telemetry_store


def _tm(name: str):
    """Shortcut: tool-decorator pre-bound to telemetry store + tool name.

    Passes `_get_telemetry` as a callable (not its result) so the store is
    resolved per-invocation — lets tests monkey-patch `_telemetry_store`."""
    return with_telemetry(_get_telemetry, tool_name=name, source="server-middleware")


def _get_client() -> httpx.AsyncClient:
    global _http, _mnemo_url, _mnemo_token
    if _http is None:
        _mnemo_url = (os.environ.get("MNEMO_BASE_URL") or "http://localhost:8080").rstrip("/")
        _mnemo_token = os.environ.get("MNEMO_TOKEN") or ""
        _http = httpx.AsyncClient(
            base_url=_mnemo_url,
            headers={"Authorization": f"Bearer {_mnemo_token}"} if _mnemo_token else {},
            timeout=httpx.Timeout(15.0),
        )
    return _http


# ---- server ----

def _transport_security():
    """Allow remote Host headers when serving HTTP. By default FastMCP's
    DNS-rebinding guard rejects everything but localhost.
    Env: ROTATOR_MCP_ALLOWED_HOSTS=host1,host2 (comma-separated).
    """
    raw = (os.environ.get("ROTATOR_MCP_ALLOWED_HOSTS") or "").strip()
    if not raw:
        return None
    try:
        from mcp.server.transport_security import TransportSecuritySettings
        hosts = [h.strip() for h in raw.split(",") if h.strip()]
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=hosts + [f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts],
        )
    except Exception:
        return None


app = FastMCP("rotator", transport_security=_transport_security())


@app.tool()
@_tm("mnemo_recall")
async def mnemo_recall(
    query: str,
    project: str | None = None,
    limit: int = 12,
    salience_min: float = 0.3,
) -> list[dict[str, Any]]:
    """Semantic recall against MNEMO. Returns top-N events ranked by
    cosine x time-decay x salience. Use this whenever the operator
    references past work / decisions / chats."""
    c = _get_client()
    params: dict[str, Any] = {"q": query, "limit": limit, "salience_min": salience_min}
    if project:
        params["project"] = project
    r = await c.get("/recall", params=params)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("events", [])


@app.tool()
@_tm("mnemo_events")
async def mnemo_events(
    project: str | None = None,
    type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    produced_by_agent: str | None = None,
) -> list[dict[str, Any]]:
    """Temporal MNEMO query. Use when operator asks about a specific
    date range or wants chronological history."""
    c = _get_client()
    params: dict[str, Any] = {"limit": limit, "include_low": "true"}
    if project:
        params["project"] = project
    if type:
        params["type"] = type
    if since:
        params["from"] = since
    if until:
        params["to"] = until
    if produced_by_agent:
        params["produced_by_agent"] = produced_by_agent
    r = await c.get("/events", params=params)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("events", [])


@app.tool()
@_tm("mnemo_event_content")
async def mnemo_event_content(event_id: str) -> dict[str, Any]:
    """Fetch the FULL transcript / body of a single event by id.
    Use for chat-events when summary alone is insufficient."""
    c = _get_client()
    r = await c.get(f"/events/{event_id}/content")
    if r.status_code == 404:
        return {"event_id": event_id, "error": "no content for this event"}
    r.raise_for_status()
    return r.json()


@app.tool()
@_tm("mnemo_write")
async def mnemo_write(event: dict[str, Any]) -> dict[str, Any]:
    """Write a new event to MNEMO. event must include id (16-hex),
    ts (ISO), type, project, summary (<=200 chars), salience (0-1),
    confidence (0-1), privacy, produced_by_agent."""
    c = _get_client()
    r = await c.post("/events", json=event)
    r.raise_for_status()
    return r.json()


@app.tool()
@_tm("mnemo_state")
async def mnemo_state() -> dict[str, Any]:
    """Return MNEMO health / counts. Use for sanity check or when
    operator asks 'how many events do we have'."""
    c = _get_client()
    r = await c.get("/state")
    r.raise_for_status()
    return r.json()


@app.tool()
@_tm("chronicle_build")
async def chronicle_build(
    level: str = "daily",
    period: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Build a hierarchical chronicle and POST it to MNEMO.
    level: daily | weekly | monthly
    period: 'YYYY-MM-DD' (daily) | 'YYYY-Www' (weekly) | 'YYYY-MM' (monthly).
            Defaults to the previous full period.
    """
    from rotator.chronicler import build_chronicle, period_key_for, period_window
    from rotator.memory.mnemo_client import MnemoClient
    from rotator.orchestrator.budget import BudgetState
    from rotator.orchestrator.llm import LLMClient, LLMConfig

    if period is None:
        now = dt.datetime.now(dt.UTC)
        if level == "daily":
            d = now.date() - dt.timedelta(days=1)
            period = d.isoformat()
        elif level == "weekly":
            d = now.date() - dt.timedelta(days=7)
            iso = d.isocalendar()
            period = f"{iso.year}-W{iso.week:02d}"
        else:  # monthly
            y = now.year
            m = now.month - 1
            if m == 0:
                y -= 1
                m = 12
            period = f"{y:04d}-{m:02d}"

    since, until = period_window(level, period)

    cfg = LLMConfig(
        sonnet_model=os.environ.get("ROTATOR_SONNET", "anthropic/claude-sonnet-4.6"),
        haiku_model=os.environ.get("ROTATOR_HAIKU", "anthropic/claude-haiku-4.5"),
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    )
    llm = LLMClient(config=cfg)
    try:
        async with MnemoClient(
            base_url=os.environ.get("MNEMO_BASE_URL", "http://localhost:8080"),
            token=os.environ.get("MNEMO_TOKEN", ""),
        ) as mnemo:
            events = await mnemo.events(
                project=project,
                since=since.strftime("%Y-%m-%d"),
                until=until.strftime("%Y-%m-%d"),
                limit=500,
                include_low=True,
            )
            res = await build_chronicle(
                events=list(events),
                llm=llm,
                project=project,
                period_key=period,
                level=level,
                budget=BudgetState(),
            )
            if res is None:
                return {"error": "no events in window", "period": period}
            await mnemo.write_event(res.event_payload)
            return {
                "level": level,
                "period_key": period,
                "summary": res.summary_head,
                "event_id": res.event_payload["id"],
                "sources": len(res.source_event_ids),
            }
    finally:
        await llm.aclose()


@app.tool()
@_tm("session_note")
def session_note(body: str) -> dict[str, Any]:
    """Append a free-form note to today's MCP session note file.
    Use when operator says 'note this' / 'make a note' / similar.
    Files live in ~/.rotator/sessions/mcp-YYYY-MM-DD.md."""
    date = dt.datetime.now().strftime("%Y-%m-%d")
    root = Path.home() / ".rotator" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"mcp-{date}.md"
    ts = dt.datetime.now().strftime("%H:%M:%S")
    if not path.exists():
        path.write_text(f"# Rotator MCP notes — {date}\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {ts}\n{body}\n")
    return {"path": str(path), "appended": True}


@app.tool()
@_tm("call_consult")
async def call_consult(state: dict[str, Any]) -> dict[str, Any]:
    """Voice Co-pilot brain — dispatches a Claude Code subprocess.

    state is a serialized ConversationState dict: {session_id, mode, turns,
    current_intent?, intent_confidence?, ...}. Returns:
      {type: "bubble"|"clarify"|"progress", bubble: str, citations: [],
       suggested_actions: [], confidence: float, follow_up?: str}

    The system prompt is built from rotator.call.prompts.system_prompt
    and includes the tool inventory + transcript window. Claude is free
    to dispatch its own MCP tools (mnemo_recall, Read, Grep, ...) in
    parallel before emitting the JSON response.
    """
    from rotator.call.consult import dispatch_consult
    return await dispatch_consult(state)


@app.tool()
@_tm("rotator_self_report")
async def rotator_self_report(
    session_id: str,
    per_tool_verdict: dict[str, str],
    notes: str = "",
    inferred_phase: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Self-report your perceived utility of rotator tools BEFORE the session ends.

    CALL THIS NEAR THE END OF EVERY CONVERSATION.

    Args:
      session_id: any stable id you can fabricate per conversation.
      per_tool_verdict: {tool_name: "helpful"|"neutral"|"waste"} for each
        rotator/mnemo tool you actually used.
      notes: free-form 1-2 sentences about what worked or didn't.
      inferred_phase: one of "impl" | "debug" | "polish" | "deploy".
      project: short project slug if obvious from context.

    Returns: {ok: bool, report_id: int} on success.
    """
    from rotator.telemetry.store import SelfReport
    import time as _time
    store = _get_telemetry()
    rid = store.insert_self_report(
        SelfReport(
            ts=_time.time(),
            session_id=session_id,
            per_tool_verdict=per_tool_verdict or {},
            notes=notes or "",
            inferred_phase=inferred_phase,
            project=project,
        )
    )
    return {"ok": True, "report_id": rid, "project": project}


@app.tool()
@_tm("skill_lookup")
async def skill_lookup(query: str | None = None) -> list[dict[str, Any]]:
    """List skills in MNEMO matching the query (or all if no query)."""
    c = _get_client()
    params: dict[str, Any] = {}
    if query:
        params["q"] = query
    try:
        r = await c.get("/skills", params=params)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError:
        return [{"error": "MNEMO /skills not available on this host"}]


def main() -> None:
    """Entrypoint. Transport via ROTATOR_MCP_TRANSPORT env (default stdio).

    Modes:
      stdio (default)     — Claude Code launches as subprocess. Local only.
      streamable-http     — HTTP+SSE on ROTATOR_MCP_HOST:ROTATOR_MCP_PORT.
                            Remote clients hit /mcp endpoint with bearer.
    """
    transport = os.environ.get("ROTATOR_MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        app.run()
        return
    if transport in ("http", "streamable-http", "sse"):
        host = os.environ.get("ROTATOR_MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("ROTATOR_MCP_PORT", "18081"))
        token = (os.environ.get("ROTATOR_MCP_TOKEN") or "").strip()
        if token:
            try:
                asgi = app.streamable_http_app()
                from starlette.middleware.base import BaseHTTPMiddleware
                from starlette.responses import JSONResponse

                class _BearerAuth(BaseHTTPMiddleware):
                    async def dispatch(self, request, call_next):
                        if request.url.path in ("/health", "/"):
                            return await call_next(request)
                        auth = request.headers.get("authorization") or ""
                        if not auth.startswith("Bearer ") or auth.split(" ", 1)[1] != token:
                            return JSONResponse({"error": "unauthorized"}, status_code=401)
                        return await call_next(request)

                asgi.add_middleware(_BearerAuth)
                import uvicorn
                uvicorn.run(asgi, host=host, port=port, log_level="info")
                return
            except Exception as e:
                print(f"[mcp_server] auth wiring failed: {e}; "
                      "starting WITHOUT auth on {host}:{port}", file=__import__("sys").stderr)
        app.run(transport="streamable-http", host=host, port=port)
        return
    raise SystemExit(f"unknown ROTATOR_MCP_TRANSPORT={transport!r}")


if __name__ == "__main__":
    main()
