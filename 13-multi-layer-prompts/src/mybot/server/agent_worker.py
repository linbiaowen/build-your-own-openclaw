"""Agent worker for executing agent jobs."""

import asyncio
import logging
from dataclasses import replace
from typing import Union

from .worker import SubscriberWorker
from mybot.core.agent import Agent
from mybot.core.events import (
    AgentEventSource,
    DispatchEvent,
    DispatchResultEvent,
    InboundEvent,
    OutboundEvent,
)
from mybot.utils.def_loader import DefNotFoundError

MAX_RETRIES = 3

logger = logging.getLogger(__name__)

ProcessableEvent = Union[InboundEvent, DispatchEvent]


class AgentWorker(SubscriberWorker):
    """Dispatches events to session executors."""

    def __init__(self, context):
        super().__init__(context)

        self.context.eventbus.subscribe(InboundEvent, self.dispatch_event)
        self.context.eventbus.subscribe(DispatchEvent, self.dispatch_event)
        self.logger.info(
            "AgentWorker subscribed to InboundEvent and DispatchEvent events"
        )

    async def dispatch_event(self, event: ProcessableEvent) -> None:
        """Create executor task for typed event."""
        session_info = self.context.history_store.get_session_info(event.session_id)
        if not session_info:
            logger.error(f"Session not found: {event.session_id}")
            return

        agent_id = session_info.agent_id

        try:
            agent_def = self.context.agent_loader.load(agent_id)
        except DefNotFoundError as e:
            logger.error(f"Agent not found: {agent_id}: {e}")
            return await self._emit_response(event, "", agent_id, str(e))

        asyncio.create_task(self.exec_session(event, agent_def))

    async def exec_session(self, event: ProcessableEvent, agent_def) -> None:
        session_id = event.session_id

        try:
            agent = Agent(agent_def, self.context)
            if session_id:
                try:
                    session = agent.load_session(session_id)
                except ValueError:
                    logger.warning(f"Session {session_id} not found, creating new")
                    session = agent.new_session(event.source, session_id=session_id)
            else:
                session = agent.new_session(event.source)
                session_id = session.session_id

            if event.content.startswith("/"):
                result = await self.context.command_registry.dispatch(
                    event.content, session
                )
                if result:
                    await self._emit_response(event, result, agent_def.id)
                    logger.info(f"Command completed: {session_id}")
                    return

            logger.info(
                f"Processing message for session {session_id} from {event.source}"
            )
            response = await session.chat(event.content)
            logger.info(f"Session completed: {session_id}")

            if isinstance(event, DispatchEvent) and session.source.is_cron:
                if session.state.cron_outbound_sent:
                    return
            await self._emit_response(event, response, agent_def.id)

        except Exception as e:
            logger.error(f"Session failed: {e}")

            if event.retry_count < MAX_RETRIES:
                retry_event = replace(
                    event,
                    retry_count=event.retry_count + 1,
                    content=".",
                )
                await self.context.eventbus.publish(retry_event)
            else:
                await self._emit_response(event, "", agent_def.id, str(e))

    async def _emit_response(
        self,
        event: ProcessableEvent,
        content: str,
        agent_id: str,
        error: str | None = None,
    ) -> None:
        """Emit response event with content."""
        if isinstance(event, DispatchEvent):
            result_event: OutboundEvent | DispatchResultEvent = DispatchResultEvent(
                session_id=event.session_id,
                source=AgentEventSource(agent_id),
                content=content,
                error=str(error) if error else None,
            )
        else:
            result_event = OutboundEvent(
                session_id=event.session_id,
                source=AgentEventSource(agent_id),
                content=content,
                error=str(error) if error else None,
            )
        await self.context.eventbus.publish(result_event)
