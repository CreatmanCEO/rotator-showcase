# Excerpt from rotator (private repo). Full source: github.com/CreatmanCEO/rotator (access on request)
# This file is provided for code review purposes — it runs in context of the full project.

"""Shared brain dispatch for call_consult — used by both local
orchestrator (Windows subprocess) and MCP tool (sw4 remote).

Reviewer flagged code duplication between orchestrator.py::_local_consult
and mcp_server.py::call_consult. This module is the single source of
truth for the dispatch logic.
"""

from __future__ import annotations

import json
import re
from typing import Any


async def dispatch_consult(state_dict: dict[str, Any]) -> dict[str, Any]:
    """Build prompt from state, invoke claude_code_run, parse JSON response.

    Returns a structured dict: {type, bubble, citations, suggested_actions,
    confidence, follow_up?} on success; graceful fallback on parse error or
    subprocess failure.
    """
    from rotator.call.prompts.system_prompt import build_prompt
    from rotator.tools.code_tools import claude_code_run

    prompt = build_prompt(state_dict)
    result = await claude_code_run(task=prompt, max_budget_usd=0.50, timeout_s=60)
    if result.error:
        return {
            "type": "bubble",
            "bubble": f"(brain error: {result.error})",
            "citations": [],
            "suggested_actions": [],
            "confidence": 0.0,
        }
    text = (result.text or "").strip()
    # Strip ```json ... ``` fences (robust regex, handles trailing text)
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "type" in parsed:
            return parsed
    except json.JSONDecodeError:
        pass
    return {
        "type": "bubble",
        "bubble": text or "(empty)",
        "citations": [],
        "suggested_actions": [],
        "confidence": 0.3,
    }
