"""Base LLM provider abstraction."""

import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional, cast

from litellm import acompletion, Choices, TYPE_CHECKING
from litellm.types.completion import ChatCompletionMessageParam as Message

if TYPE_CHECKING:
    from mybot.utils.config import LLMConfig

_TEXT_CONTENT_KEYS = ("content", "message", "text", "response", "answer", "reply")

_STRUCTURED_LEAK_MARKERS = (
    '"tool_calls"',
    '"tool_name"',
    '"tool_args"',
    '"function_call"',
    '"thought"',
    '"name"',
    '"arguments"',
)


def _extract_text_value(data: dict[str, Any]) -> str | None:
    for key in _TEXT_CONTENT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def looks_like_structured_leak(content: str) -> bool:
    """True when the model put structured JSON (tools, thoughts, etc.) in content."""
    stripped = content.strip()
    if not stripped.startswith("{"):
        return False
    return any(marker in stripped for marker in _STRUCTURED_LEAK_MARKERS)


def looks_like_tool_call_leak(content: str) -> bool:
    """Alias for looks_like_structured_leak."""
    return looks_like_structured_leak(content)


def parse_leaked_tool_calls(content: str) -> list["LLMToolCall"]:
    """Parse tool calls some models embed in the content field instead of tool_calls."""
    stripped = content.strip()
    if not stripped.startswith("{"):
        return []

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, dict):
        return []

    raw_calls = data.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []

    parsed: list[LLMToolCall] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            continue

        name = (
            item.get("tool_name")
            or item.get("name")
            or (item.get("function") or {}).get("name")
        )
        if not isinstance(name, str) or not name:
            continue

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

        parsed.append(
            LLMToolCall(
                id=item.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                name=name,
                arguments=args_str,
            )
        )

    return parsed


def normalize_assistant_content(content: str) -> str:
    """Unwrap JSON-shaped assistant replies some local models emit instead of plain text."""
    if not content:
        return content

    stripped = content.strip()
    if not stripped.startswith("{"):
        return content

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        if looks_like_structured_leak(stripped):
            return ""
        return content

    if not isinstance(data, dict):
        return content

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
