# Excerpt from rotator (private repo). Full source: github.com/CreatmanCEO/rotator (access on request)
# This file is provided for code review purposes — it runs in context of the full project.

"""Telemetry middleware — wraps tool functions with timing + result logging.

Usage in mcp_server.py:

    from rotator.telemetry import middleware as tm
    from rotator.telemetry.store import TelemetryStore, default_store_path

    _store = TelemetryStore(default_store_path())
    _store.init()

    @app.tool()
    @tm.with_telemetry(_store, tool_name="mnemo_recall", source="server-middleware")
    async def mnemo_recall(...):
        ...

The wrapper is fail-soft — if telemetry write blows up (disk full,
schema mismatch), the tool's return value still propagates. We never
let observability break the system being observed.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
import socket
import time
from typing import Any, Awaitable, Callable, Optional

from rotator.telemetry.store import TelemetryStore, ToolCall


def args_hash(kwargs: dict[str, Any]) -> str:
    """16-char sha256 prefix of the kwargs, deterministic + order-independent."""
    canonical = json.dumps(kwargs, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _client_host_default() -> str:
    return os.environ.get("ROTATOR_CLIENT_HOST") or socket.gethostname()


def _result_size(result: Any) -> tuple[int, bool]:
    """Estimate the byte-size of a tool result; truncate flag if huge."""
    try:
        encoded = json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(result).encode("utf-8")
    size = len(encoded)
    return size, size > 64 * 1024


def with_telemetry(
    store: TelemetryStore | Callable[[], TelemetryStore],
    *,
    tool_name: str,
    source: str = "server-middleware",
    session_id_getter: Optional[Callable[[], Optional[str]]] = None,
    client_host_getter: Optional[Callable[[], Optional[str]]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a tool function (sync or async) with timing + telemetry logging.

    `store` may be a TelemetryStore OR a zero-arg callable returning one. The
    callable form is resolved per-invocation, which lets you monkey-patch the
    module-level store in tests and have wrapped functions pick up the new
    reference.
    """

    def _resolve_store() -> TelemetryStore:
        return store() if callable(store) else store

    def _record(error: Optional[str], result: Any, t0: float, kwargs: dict) -> None:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        size, truncated = _result_size(result) if error is None else (0, False)
        tc = ToolCall(
            ts=time.time(),
            source=source,
            client_host=(client_host_getter() if client_host_getter else _client_host_default()),
            session_id=(session_id_getter() if session_id_getter else None),
            tool_name=tool_name,
            args_hash=args_hash(kwargs),
            latency_ms=latency_ms,
            result_size_bytes=size,
            result_truncated=truncated,
            error=error,
        )
        try:
            _resolve_store().insert_tool_call(tc)
        except Exception:  # noqa: BLE001 — never let telemetry break the tool
            pass

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapped_async(*args, **kwargs):
                t0 = time.perf_counter()
                error: Optional[str] = None
                result: Any = None
                try:
                    result = await fn(*args, **kwargs)
                    return result
                except BaseException as e:
                    error = f"{type(e).__name__}: {e}"
                    raise
                finally:
                    _record(error, result, t0, kwargs)
            return wrapped_async

        @functools.wraps(fn)
        def wrapped_sync(*args, **kwargs):
            t0 = time.perf_counter()
            error: Optional[str] = None
            result: Any = None
            try:
                result = fn(*args, **kwargs)
                return result
            except BaseException as e:
                error = f"{type(e).__name__}: {e}"
                raise
            finally:
                _record(error, result, t0, kwargs)
        return wrapped_sync

    return deco
