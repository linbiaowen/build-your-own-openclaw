"""Post message tool factory for agent-initiated messaging."""

import time
from typing import TYPE_CHECKING

from mybot.core.events import AgentEventSource, OutboundEvent
from mybot.tools.base import BaseTool, tool

if TYPE_CHECKING:
    from mybot.core.agent import AgentSession
    from mybot.core.context import SharedContext


def create_post_message_tool(context: "SharedContext") -> BaseTool | None:
    """Factory to create post_message tool."""
    config = context.config

    if not config.channels.enabled:
        return None

    if not context.channels:
        return None

    @tool(
        name="post_message",
        description=(
            "Send a message to the user via the default messaging platform. "
            "Use for cron results, reminders, and proactive notifications."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The message content to send to the user",
                }
            },
            "required": ["content"],
        },
    )
    async def post_message(content: str, session: "AgentSession") -> str:
        """Send a message to the default user on the default platform."""
        try:
            event = OutboundEvent(
                session_id=session.session_id,
                source=AgentEventSource(agent_id=session.agent.agent_def.id),
                content=content,
                timestamp=time.time(),
            )
            await context.eventbus.publish(event)
            return "Message queued for delivery"
        except Exception as e:
            return f"Failed to send message: {e}"

    return post_message
