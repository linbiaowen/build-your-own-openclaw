"""Base LLM provider abstraction."""

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional, cast

from litellm import acompletion, Choices, TYPE_CHECKING
from litellm.types.completion import ChatCompletionMessageParam as Message

if TYPE_CHECKING:
    from mybot.utils.config import LLMConfig

_TEXT_CONTENT_KEYS = (
    "content",
    "message",
    "text",
    "response",
    "answer",
    "reply",
    "gemma_response",
)

_STRUCTURED_LEAK_MARKERS = (
    '"tool_calls"',
    '"tool_name"',
    '"tool_args"',
    '"function_call"',
    '"thought"',
    '"name"',
    '"arguments"',
)

# Gemma often emits {"content": "..."} or {"gemma_response": "..."} (even when truncated)
_WRAPPER_VALUE_RE = re.compile(
    r'"(?:content|gemma_response|message|text|response|answer|reply)"\s*:\s*"((?:[^"\\]|\\.)*)',
    re.DOTALL,
)
_JSON_KEY_VALUE_RE = re.compile(r'"\w+"\s*:\s*"')


def _unescape_json_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace("\\n", "\n").replace('\\"', '"').replace("\\t", "\t")


def _unwrap_wrapped_string(content: str) -> str | None:
    """Extract plain text from wrapper JSON, including truncated gemma responses."""
    match = _WRAPPER_VALUE_RE.search(content)
    if not match:
        return None
    text = _unescape_json_string(match.group(1))
    return text if text.strip() else None


def _extract_text_value(data: dict[str, Any]) -> str | None:
    for key in _TEXT_CONTENT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def looks_like_structured_leak(content: str) -> bool:
    """True when the model put structured JSON (tools, thoughts, etc.) in content."""
    if re.search(r"\bTool\s+Calls?\s*:\s*\[", content, re.IGNORECASE):
        return True

    stripped = content.strip()
    if not stripped.startswith("{"):
        return False
    if any(marker in stripped for marker in _STRUCTURED_LEAK_MARKERS):
        return True
    if _JSON_KEY_VALUE_RE.search(stripped):
        if _unwrap_wrapped_string(stripped):
            return False
        return True
    return False


def looks_like_tool_call_leak(content: str) -> bool:
    """Alias for looks_like_structured_leak."""
    return looks_like_structured_leak(content)


def _tool_call_from_dict(item: dict[str, Any]) -> "LLMToolCall | None":
    name = (
        item.get("tool_name")
        or item.get("name")
        or (item.get("function") or {}).get("name")
    )
    if not isinstance(name, str) or not name:
        return None

    args = (
        item.get("tool_args")
        or item.get("arguments")
        or (item.get("function") or {}).get("arguments")
        or {}
    )
    if isinstance(args, str):
        args_str = args
    else:
        args_str = json.dumps(args)

    call_id = item.get("id")
    if not isinstance(call_id, str) or not call_id:
        call_id = f"call_{uuid.uuid4().hex[:12]}"

    return LLMToolCall(id=call_id, name=name, arguments=args_str)


def _extract_json_array_from_index(content: str, start: int) -> list[Any] | None:
    """Parse a JSON array starting at `start` (handles trailing prose)."""
    try:
        value, _ = json.JSONDecoder().raw_decode(content, start)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, list) else None


def _find_embedded_tool_call_array(content: str) -> list[Any] | None:
    """Find tool call arrays gemma prints as prose, e.g. 'Tool Calls: [...]'."""
    markers = (
        r"Tool\s+Calls?\s*:\s*",
        r'"tool_calls"\s*:\s*',
        r"'tool_calls'\s*:\s*",
    )
    for marker in markers:
        match = re.search(marker, content, re.IGNORECASE)
        if not match:
            continue
        bracket = content.find("[", match.end())
        if bracket < 0:
            continue
        arr = _extract_json_array_from_index(content, bracket)
        if arr:
            return arr
    return None


def strip_leaked_tool_call_block(content: str) -> str:
    """Drop 'Tool Calls: [...]' sections from user-visible assistant text."""
    match = re.search(r"\n\s*Tool\s+Calls?\s*:", content, re.IGNORECASE)
    if match:
        return content[: match.start()].strip()
    if re.match(r"^\s*Tool\s+Calls?\s*:", content, re.IGNORECASE):
        return ""
    return content.strip()


def parse_leaked_tool_calls(content: str) -> list["LLMToolCall"]:
    """Parse tool calls some models embed in the content field instead of tool_calls."""
    raw_lists: list[Any] = []

    stripped = content.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            tc = data.get("tool_calls")
            if isinstance(tc, list):
                raw_lists.append(tc)

    embedded = _find_embedded_tool_call_array(content)
    if embedded:
        raw_lists.append(embedded)

    parsed: list[LLMToolCall] = []
    for raw_calls in raw_lists:
        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            call = _tool_call_from_dict(item)
            if call:
                parsed.append(call)

    return parsed


def normalize_assistant_content(content: str) -> str:
    """Unwrap JSON-shaped assistant replies some local models emit instead of plain text."""
    if not content:
        return content

    stripped = content.strip()
    if not stripped.startswith("{"):
        return content

    unwrapped = _unwrap_wrapped_string(stripped)
    if unwrapped:
        return unwrapped

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        if looks_like_structured_leak(stripped):
            return ""
        return content

    if not isinstance(data, dict):
        return content

    if len(data) == 1:
        val = next(iter(data.values()))
        if isinstance(val, str) and val.strip():
            return val

    text = _extract_text_value(data)
    if text:
        return text

    arguments = data.get("arguments")
    if isinstance(arguments, dict):
        text = _extract_text_value(arguments)
        if text:
            return text
    elif isinstance(arguments, str) and arguments.strip():
        return arguments

    if data.get("tool_calls") or data.get("thought"):
        return ""

    if looks_like_structured_leak(stripped):
        return ""

    return content


@dataclass
class LLMToolCall:
    """A tool/function call from the LLM."""

    id: str
    name: str
    arguments: str  # JSON string


class LLMProvider:
    """LLM provider using litellm for multi-provider support."""

    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ):
        """Initialize LLM provider."""
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._settings = kwargs

    @classmethod
    def from_config(cls, config: "LLMConfig") -> "LLMProvider":
        """Create provider from LLMConfig."""
        return cls(
            model=config.model,
            api_key=config.api_key,
            api_base=config.api_base,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> tuple[str, list[LLMToolCall]]:
        """Default implementation using litellm. Subclasses can override."""
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "api_key": self.api_key,
        }

        if self.api_base:
            request_kwargs["api_base"] = self.api_base
        if tools:
            request_kwargs["tools"] = tools
        request_kwargs.update(kwargs)

        response = await acompletion(**request_kwargs)

        message = cast(Choices, response.choices[0]).message
        raw_content = message.content or ""
        tool_calls = [
            LLMToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=tc["function"]["arguments"],
            )
            for tc in (message.tool_calls or [])
        ]

        if not tool_calls:
            tool_calls = parse_leaked_tool_calls(raw_content)

        content = normalize_assistant_content(raw_content)

        return (content, tool_calls)
