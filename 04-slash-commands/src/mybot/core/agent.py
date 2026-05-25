"""Agent and AgentSession for step 04 with slash commands support."""

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from litellm.types.completion import (
    ChatCompletionMessageParam as Message,
    ChatCompletionMessageToolCallParam,
)

from mybot.core.commands.registry import CommandRegistry
from mybot.core.history import HistoryStore
from mybot.core.session_state import SessionState
from mybot.core.cron_loader import CronLoader
from mybot.core.skill_loader import SkillLoader
from mybot.provider.llm import LLMProvider, LLMToolCall
from mybot.provider.llm.base import looks_like_structured_leak
from mybot.tools.registry import ToolRegistry
from mybot.tools.skill_tool import create_skill_tool

if TYPE_CHECKING:
    from mybot.core.agent_loader import AgentDef
    from mybot.utils.config import Config

MAX_TOOL_ITERATIONS = 5

_CAPABILITIES_LIST_RE = re.compile(
    r"\b(what skills|which skills|list skills|available skills|what tools|which tools|list tools)\b",
    re.IGNORECASE,
)


class Agent:
    """A configured agent that creates and manages conversation sessions."""

    def __init__(self, agent_def: "AgentDef", config: "Config") -> None:
        self.agent_def = agent_def
        self.config = config
        self.llm = LLMProvider.from_config(agent_def.llm)
        self.skill_loader = SkillLoader.from_config(config)
        self.cron_loader = CronLoader.from_config(config)
        self.history_store = HistoryStore.from_config(config)
        self.command_registry = CommandRegistry.with_builtins()

    def _build_tools(self) -> ToolRegistry:
        """Build a ToolRegistry with tools appropriate for the session."""
        registry = ToolRegistry.with_builtins()

        if self.agent_def.allow_skills:
            skill_tool = create_skill_tool(self.skill_loader)
            if skill_tool:
                registry.register(skill_tool)

        return registry

    def _make_session(
        self,
        session_id: str,
        messages: list[Message],
    ) -> "AgentSession":
        tools = self._build_tools()
        state = SessionState(
            session_id=session_id,
            agent=self,
            messages=messages,
            history_store=self.history_store,
        )
        return AgentSession(
            agent=self,
            state=state,
            tools=tools,
            command_registry=self.command_registry,
        )

    def new_session(self, session_id: str | None = None) -> "AgentSession":
        """Create a new conversation session."""
        session_id = session_id or str(uuid.uuid4())
        session = self._make_session(session_id, [])
        self.history_store.create_session(self.agent_def.id, session_id)
        return session

    def load_session(self, session_id: str) -> "AgentSession":
        """Resume an existing session with messages loaded from history."""
        info = self.history_store.get_session_info(session_id)
        if info is None:
            raise ValueError(f"Session not found: {session_id}")

        messages = [
            hm.to_message() for hm in self.history_store.get_messages(session_id)
        ]
        self.history_store.set_active_session(self.agent_def.id, session_id)
        return self._make_session(session_id, messages)

    def resume_active_session(self) -> "AgentSession | None":
        """Resume the active session for this agent, or the most recent one."""
        active_id = self.history_store.get_active_session_id(self.agent_def.id)
        if active_id:
            return self.load_session(active_id)

        latest = self.history_store.get_latest_session(self.agent_def.id)
        if latest is None:
            return None
        return self.load_session(latest.id)


@dataclass
class AgentSession:
    """Chat orchestrator - operates on swappable SessionState."""

    agent: Agent
    state: SessionState
    tools: ToolRegistry
    command_registry: CommandRegistry
    started_at: datetime = field(default_factory=datetime.now)

    @property
    def session_id(self) -> str:
        """Delegate to state."""
        return self.state.session_id

    def _wants_capabilities_list(self, message: str) -> bool:
        return bool(_CAPABILITIES_LIST_RE.search(message))

    def _format_capabilities_list(self) -> str:
        """Plain-text list of built-in tools and available skills."""
        lines = ["Here are the tools and skills I can use:\n", "**Built-in tools**"]
        for tool in self.tools.list_all():
            if tool.name == "skill":
                continue
            lines.append(f"- **{tool.name}**: {tool.description}")

        skills = self.agent.skill_loader.discover_skills()
        if skills:
            lines.append("\n**Skills** (load full instructions with the `skill` tool):")
            for skill in skills:
                lines.append(f"- **{skill.name}** (`{skill.id}`): {skill.description}")

        return "\n".join(lines)

    async def chat(self, message: str) -> str:
        """Send a message to the LLM and get a response."""
        user_msg: Message = {"role": "user", "content": message}
        self.state.add_message(user_msg)

        if self._wants_capabilities_list(message):
            content = self._format_capabilities_list()
            self.state.add_message({"role": "assistant", "content": content})
            return content

        tool_schemas = self.tools.get_tool_schemas()
        content = ""

        for _ in range(MAX_TOOL_ITERATIONS):
            messages = self.state.build_messages()
            content, tool_calls = await self.agent.llm.chat(messages, tool_schemas)

            if not tool_calls:
                if not content.strip() or looks_like_structured_leak(content):
                    if self._wants_capabilities_list(message):
                        content = self._format_capabilities_list()
                    else:
                        content = await self._chat_without_tools()
                self.state.add_message({"role": "assistant", "content": content})
                break

            if not any(self.tools.get(tc.name) for tc in tool_calls):
                content = await self._chat_without_tools()
                break

            tool_call_dicts: list[ChatCompletionMessageToolCallParam] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in tool_calls
            ]
            assistant_msg: Message = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_call_dicts,
            }
            self.state.add_message(assistant_msg)
            await self._handle_tool_calls(tool_calls)

        else:
            if self._wants_capabilities_list(message):
                content = self._format_capabilities_list()
            else:
                content = await self._chat_without_tools()

        if looks_like_structured_leak(content):
            if self._wants_capabilities_list(message):
                content = self._format_capabilities_list()
            else:
                content = await self._chat_without_tools()

        return content

    async def _chat_without_tools(self) -> str:
        """Ask the LLM for a plain-text reply when tool calling fails."""
        messages = self.state.build_messages()
        content, _ = await self.agent.llm.chat(messages, tools=None)
        self.state.add_message({"role": "assistant", "content": content})
        return content

    async def _handle_tool_calls(
        self,
        tool_calls: list["LLMToolCall"],
    ) -> None:
        """Handle tool calls from the LLM response."""
        tool_call_results = await asyncio.gather(
            *[self._execute_tool_call(tool_call) for tool_call in tool_calls]
        )

        for tool_call, result in zip(tool_calls, tool_call_results):
            tool_msg: Message = {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call.id,
            }
            self.state.add_message(tool_msg)

    async def _execute_tool_call(
        self,
        tool_call: LLMToolCall,
    ) -> str:
        """Execute a single tool call."""
        try:
            args = json.loads(tool_call.arguments)
        except json.JSONDecodeError:
            args = {}

        try:
            result = await self.tools.execute_tool(tool_call.name, session=self, **args)
        except Exception as e:
            result = f"Error executing tool: {e}"

        return result
