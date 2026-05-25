"""Session state container with persistence helpers."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from litellm.types.completion import ChatCompletionMessageParam as Message

from mybot.core.history import HistoryMessage

if TYPE_CHECKING:
    from mybot.core.agent import Agent
    from mybot.core.context import SharedContext


@dataclass
class SessionState:
    """Pure conversation state + persistence."""

    session_id: str
    agent: "Agent"
    messages: list[Message]
    shared_context: "SharedContext"

    def add_message(self, message: Message) -> None:
        """Add message to in-memory list + persist."""
        self.messages.append(message)
        history_msg = HistoryMessage.from_message(message)
        self.shared_context.history_store.save_message(self.session_id, history_msg)

    def build_messages(self) -> list[Message]:
        """Build messages list with system prompt."""
        system_prompt = self.agent.agent_def.agent_md

        if self.agent.agent_def.allow_skills:
            skills = self.shared_context.skill_loader.discover_skills()
            if skills:
                skill_lines = "\n".join(
                    f"- **{skill.name}** (`{skill.id}`): {skill.description}"
                    for skill in skills
                )
                system_prompt += (
                    "\n\n## Available Skills\n"
                    f"{skill_lines}\n\n"
                    "Use the `skill` tool to load a skill's full instructions when needed."
                )

        system_prompt += (
            "\n\nAlways respond in plain natural language. "
            "Never output JSON, tool_calls, or other structured formats unless the user explicitly asks."
        )

        messages: list[Message] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.messages)
        return messages
