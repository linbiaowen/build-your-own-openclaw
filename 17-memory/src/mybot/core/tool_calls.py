"""Normalize tool-call records from LLMs (gemma often emits `function` as a JSON string)."""

from __future__ import annotations

import json
import uuid
from typing import Any


def normalize_function_field(function: Any) -> dict[str, Any]:
    """Return a {name, arguments} dict; never a bare string."""
    if isinstance(function, dict):
        return function
    if isinstance(function, str):
        stripped = function.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    return {}


def normalize_tool_call_record(tc: Any) -> dict[str, Any] | None:
    """Normalize one OpenAI-style tool_call dict for storage / compaction."""
    if isinstance(tc, str):
        try:
            tc = json.loads(tc)
        except json.JSONDecodeError:
            return None
    if not isinstance(tc, dict):
        return None

    fn = normalize_function_field(tc.get("function"))
    name = fn.get("name") or tc.get("name") or tc.get("tool_name") or ""
    args = fn.get("arguments")
    if args is None:
        args = tc.get("arguments") or tc.get("tool_args") or "{}"
    if not isinstance(args, str):
        args = json.dumps(args)

    call_id = tc.get("id")
    if not isinstance(call_id, str) or not call_id:
        call_id = f"call_{uuid.uuid4().hex[:12]}"

    return {
        "id": call_id,
        "type": tc.get("type", "function"),
        "function": {"name": name, "arguments": args},
    }


def tool_call_function_name(tc: Any, default: str = "unknown") -> str:
    """Safe function name for logging / compaction."""
    rec = normalize_tool_call_record(tc)
    if not rec:
        return default
    name = rec.get("function", {}).get("name")
    return name if isinstance(name, str) and name else default
