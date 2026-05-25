"""Agent and AgentSession for step 10 with WebSocket, channels, and web tools support."""

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

from mybot.core.context_guard import ContextGuard
from mybot.core.events import EventSource
from mybot.core.session_state import SessionState
from mybot.provider.llm import LLMProvider, LLMToolCall
from mybot.provider.llm.base import looks_like_structured_leak
from mybot.tools.registry import ToolRegistry
from mybot.tools.skill_tool import create_skill_tool
from mybot.tools.websearch_tool import create_websearch_tool
from mybot.tools.webread_tool import create_webread_tool
from mybot.tools.post_message_tool import create_post_message_tool
from mybot.core.cron_schedule import (
    build_default_notify_prompt,
    create_cron_job,
    cron_ops_system_addon,
    infer_schedule,
    infer_schedule_and_one_off,
    substitute_skill_templates,
    suggest_cron_id,
    wants_schedule_request,
)

if TYPE_CHECKING:
    from mybot.core.context import SharedContext
    from mybot.core.agent_loader import AgentDef

MAX_TOOL_ITERATIONS = 5

_CAPABILITIES_LIST_RE = re.compile(
    r"\b(what skills|which skills|list skills|available skills|what tools|which tools|list tools)\b",
    re.IGNORECASE,
)

_CRON_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Agent:
    """A configured agent that creates and manages conversation sessions."""

    def __init__(self, agent_def: "AgentDef", context: "SharedContext") -> None:
        self.agent_def = agent_def
        self.context = context
        self.llm = LLMProvider.from_config(agent_def.llm)

    def _build_tools(self, *, include_post_message: bool = False) -> ToolRegistry:
        """Build a ToolRegistry with tools appropriate for the session."""
        registry = ToolRegistry.with_builtins()

        if self.agent_def.allow_skills:
            skill_tool = create_skill_tool(self.context.skill_loader)
            if skill_tool:
                registry.register(skill_tool)

        websearch_tool = create_websearch_tool(self.context)
        if websearch_tool:
            registry.register(websearch_tool)

        webread_tool = create_webread_tool(self.context)
        if webread_tool:
            registry.register(webread_tool)

        if include_post_message:
            post_tool = create_post_message_tool(self.context)
            if post_tool:
                registry.register(post_tool)

        return registry

    def _get_token_threshold(self) -> int:
        """Get token threshold based on model's context window."""
        return 160000

    def _make_session(
        self,
        session_id: str,
        messages: list[Message],
        source: EventSource,
    ) -> "AgentSession":
        tools = self._build_tools(include_post_message=source.is_cron)
        context_guard = ContextGuard(
            shared_context=self.context,
            token_threshold=self._get_token_threshold(),
        )
        state = SessionState(
            session_id=session_id,
            agent=self,
            messages=messages,
            source=source,
            shared_context=self.context,
        )
        return AgentSession(
            agent=self,
            state=state,
            context_guard=context_guard,
            tools=tools,
        )

    def new_session(
        self,
        source: EventSource,
        session_id: str | None = None,
    ) -> "AgentSession":
        """Create a new conversation session."""
        session_id = session_id or str(uuid.uuid4())
        session = self._make_session(session_id, [], source)
        self.context.history_store.create_session(
            self.agent_def.id, session_id, source
        )
        return session

    def load_session(self, session_id: str) -> "AgentSession":
        """Resume an existing session with messages loaded from history."""
        info = self.context.history_store.get_session_info(session_id)
        if info is None:
            raise ValueError(f"Session not found: {session_id}")

        source = info.get_source()
        messages = [
            hm.to_message()
            for hm in self.context.history_store.get_messages(session_id)
        ]
        self.context.history_store.set_active_session(
            self.agent_def.id, session_id, source
        )
        return self._make_session(session_id, messages, source)

    def resume_active_session(
        self, source: EventSource
    ) -> "AgentSession | None":
        """Resume the active session for this agent/source, or the most recent."""
        active_id = self.context.history_store.get_active_session_id(
            self.agent_def.id, source
        )
        if active_id:
            return self.load_session(active_id)

        latest = self.context.history_store.get_latest_session(
            self.agent_def.id, source
        )
        if latest is None:
            return None
        return self.load_session(latest.id)

    def resume_session(self, session_id: str) -> "AgentSession":
        """Alias for load_session (used by AgentWorker)."""
        return self.load_session(session_id)


@dataclass
class AgentSession:
    """Chat orchestrator - operates on swappable SessionState."""

    agent: Agent
    state: SessionState
    context_guard: ContextGuard
    tools: ToolRegistry
    started_at: datetime = field(default_factory=datetime.now)

    @property
    def session_id(self) -> str:
        """Delegate to state."""
        return self.state.session_id

    @property
    def source(self) -> EventSource:
        return self.state.source

    @property
    def shared_context(self) -> "SharedContext":
        """Delegate to state."""
        return self.state.shared_context

    def _wants_capabilities_list(self, message: str) -> bool:
        return bool(_CAPABILITIES_LIST_RE.search(message))

    def _cron_ops_addon(self) -> str:
        try:
            skill = self.shared_context.skill_loader.load_skill("cron-ops")
            content = substitute_skill_templates(
                skill.content, self.shared_context.config
            )
        except Exception:
            content = (
                f"Create CRON.md under `{self.shared_context.config.crons_path}/<id>/` "
                "with name, description, agent, schedule, and task prompt."
            )
        return cron_ops_system_addon(self.shared_context.config, content)

    def _existing_cron_ids(self) -> set[str]:
        return {c.id for c in self.shared_context.cron_loader.discover_crons()}

    def _try_create_cron_fallback(self, message: str) -> str | None:
        """Create a cron job when the model did not (weak tool-calling models)."""
        config = self.shared_context.config
        cron_id = suggest_cron_id(message)
        if not _CRON_ID_RE.match(cron_id):
            cron_id = "scheduled-task"

        base = cron_id
        n = 2
        existing = self._existing_cron_ids()
        while cron_id in existing:
            cron_id = f"{base}-{n}"
            n += 1

        schedule, one_off = infer_schedule_and_one_off(message)
        agent_id = self.agent.agent_def.id
        prompt = build_default_notify_prompt(message)

        create_cron_job(
            config,
            cron_id=cron_id,
            name=cron_id.replace("-", " ").title(),
            description=f"Scheduled task: {message[:80]}",
            agent=agent_id,
            schedule=schedule,
            prompt=prompt,
            one_off=one_off,
        )
        when = "once" if one_off else "on schedule"
        return (
            f"✓ Scheduled cron job `{cron_id}` ({schedule}, {when}) — "
            f"I'll message you via `post_message` when it runs."
        )

    def _format_capabilities_list(self) -> str:
        """Plain-text list of built-in tools and available skills."""
        lines = ["Here are the tools and skills I can use:\n", "**Built-in tools**"]
        for tool in self.tools.list_all():
            if tool.name == "skill":
                continue
            lines.append(f"- **{tool.name}**: {tool.description}")

        skills = self.shared_context.skill_loader.discover_skills()
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

        schedule_request = wants_schedule_request(message)
        crons_before = self._existing_cron_ids() if schedule_request else set()

        if schedule_request and self.agent.agent_def.allow_skills:
            self.state.ephemeral_system_addon = self._cron_ops_addon()

        tool_schemas = self.tools.get_tool_schemas()
        content = ""

        try:
            for _ in range(MAX_TOOL_ITERATIONS):
                self.state = await self.context_guard.check_and_compact(self.state)
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

            if schedule_request:
                crons_after = self._existing_cron_ids()
                if not crons_after - crons_before:
                    fallback = self._try_create_cron_fallback(message)
                    if fallback:
                        content = fallback
                        self.state.add_message({"role": "assistant", "content": content})

            return content
        finally:
            self.state.ephemeral_system_addon = ""

    async def _chat_without_tools(self) -> str:
        """Ask the LLM for a plain-text reply when tool calling fails."""
        self.state = await self.context_guard.check_and_compact(self.state)
        messages = self.state.build_messages()
        content, _ = await self.agent.llm.chat(messages, tools=None)
        if not content.strip() or looks_like_structured_leak(content):
            content = (
                "Hi! I'm Pickle, your cat assistant. "
                "What would you like help with?"
            )
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
        tool_call: "LLMToolCall",
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
