"""Microsoft Teams channel implementation via Bot Framework."""

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

from aiohttp import web
from aiohttp.web import Request, Response
from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.core.integration import aiohttp_error_middleware
from botbuilder.integration.aiohttp import (
    CloudAdapter,
    ConfigurationBotFrameworkAuthentication,
)
from botbuilder.schema import ActivityTypes, ConversationReference

from mybot.channel.base import Channel
from mybot.core.events import EventSource
from mybot.utils.config import TeamsConfig

logger = logging.getLogger(__name__)


class _BotFrameworkConfig:
    """Adapter config object for ConfigurationBotFrameworkAuthentication."""

    def __init__(self, config: TeamsConfig):
        self.APP_ID = config.app_id
        self.APP_PASSWORD = config.app_password
        self.APP_TYPE = config.app_type
        self.APP_TENANTID = config.app_tenant_id or ""


@dataclass
class TeamsEventSource(EventSource):
    """Source for Microsoft Teams-originated events."""

    _namespace = "platform-teams"
    user_id: str
    conversation_id: str
    service_url: str
    channel_id: str
    bot_id: str
    tenant_id: str | None = None

    def __str__(self) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "service_url": self.service_url,
                    "channel_id": self.channel_id,
                    "bot_id": self.bot_id,
                    "tenant_id": self.tenant_id,
                }
            ).encode()
        ).decode().rstrip("=")
        return f"platform-teams:{self.user_id}:{self.conversation_id}:{payload}"

    @classmethod
    def from_string(cls, s: str) -> "TeamsEventSource":
        _, user_id, conversation_id, payload = s.split(":", 3)
        pad = "=" * (-len(payload) % 4)
        meta = json.loads(base64.urlsafe_b64decode(payload + pad))
        return cls(
            user_id=user_id,
            conversation_id=conversation_id,
            service_url=meta["service_url"],
            channel_id=meta["channel_id"],
            bot_id=meta["bot_id"],
            tenant_id=meta.get("tenant_id"),
        )

    @property
    def platform_name(self) -> str:
        return "teams"

    def to_conversation_reference(self) -> ConversationReference:
        from botbuilder.schema import ChannelAccount, ConversationAccount

        return ConversationReference(
            service_url=self.service_url,
            channel_id=self.channel_id,
            conversation=ConversationAccount(
                id=self.conversation_id,
                tenant_id=self.tenant_id,
            ),
            user=ChannelAccount(id=self.user_id),
            bot=ChannelAccount(id=self.bot_id),
        )

    @classmethod
    def from_turn_context(cls, turn_context: TurnContext) -> "TeamsEventSource":
        activity = turn_context.activity
        ref = TurnContext.get_conversation_reference(activity)
        conversation = activity.conversation
        recipient = activity.recipient
        sender = activity.from_property

        if not conversation or not sender or not recipient or not ref.service_url:
            raise ValueError("Incomplete Teams activity for EventSource")

        return cls(
            user_id=sender.id,
            conversation_id=conversation.id,
            service_url=ref.service_url,
            channel_id=ref.channel_id or activity.channel_id or "msteams",
            bot_id=recipient.id,
            tenant_id=getattr(conversation, "tenant_id", None),
        )


class _TeamsMessageBot(ActivityHandler):
    def __init__(self, channel: "TeamsChannel"):
        self._channel = channel

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        activity = turn_context.activity
        if activity.type != ActivityTypes.message:
            return

        text = activity.text
        if not text:
            return

        TurnContext.remove_recipient_mention(activity)
        text = text.strip()
        if not text:
            return

        try:
            source = TeamsEventSource.from_turn_context(turn_context)
        except ValueError as e:
            logger.warning("Skipping Teams message: %s", e)
            return

        logger.info(
            "Received Teams message from user %s in conversation %s",
            source.user_id,
            source.conversation_id,
        )

        if self._channel._on_message:
            try:
                await self._channel._on_message(text, source)
            except Exception as e:
                logger.error("Error in Teams message callback: %s", e)


class TeamsChannel(Channel[TeamsEventSource]):
    """Microsoft Teams platform implementation using Bot Framework."""

    platform_name = "teams"

    def __init__(self, config: TeamsConfig):
        self.config = config
        self._on_message: Callable[[str, TeamsEventSource], Awaitable[None]] | None = (
            None
        )
        self._adapter: CloudAdapter | None = None
        self._bot: _TeamsMessageBot | None = None
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._stop_event: asyncio.Event | None = None

    def is_allowed(self, source: TeamsEventSource) -> bool:
        if not self.config.allowed_user_ids:
            return True
        return source.user_id in self.config.allowed_user_ids

    async def run(
        self, on_message: Callable[[str, TeamsEventSource], Awaitable[None]]
    ) -> None:
        if self._runner is not None:
            raise RuntimeError("TeamsChannel already running")

        logger.info("Channel enabled with platform: %s", self.platform_name)
        self._on_message = on_message
        self._stop_event = asyncio.Event()

        bot_config = _BotFrameworkConfig(self.config)
        self._adapter = CloudAdapter(
            ConfigurationBotFrameworkAuthentication(bot_config)
        )
        self._bot = _TeamsMessageBot(self)

        async def messages(req: Request) -> Response:
            assert self._adapter is not None and self._bot is not None
            return await self._adapter.process(req, self._bot)

        self._app = web.Application(middlewares=[aiohttp_error_middleware])
        self._app.router.add_post("/api/messages", messages)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner, self.config.host, self.config.port
        )
        await self._site.start()

        logger.info(
            "TeamsChannel listening on http://%s:%s/api/messages",
            self.config.host,
            self.config.port,
        )

        await self._stop_event.wait()

    async def reply(self, content: str, source: TeamsEventSource) -> None:
        if not self._adapter:
            raise RuntimeError("TeamsChannel not started")

        conversation_reference = source.to_conversation_reference()

        async def send(turn_context: TurnContext) -> None:
            await turn_context.send_activity(MessageFactory.text(content))

        await self._adapter.continue_conversation(
            conversation_reference,
            send,
            self.config.app_id,
        )
        logger.debug("Sent Teams reply to conversation %s", source.conversation_id)

    async def stop(self) -> None:
        if self._runner is None:
            logger.debug("TeamsChannel not running, skipping stop")
            return

        if self._stop_event:
            self._stop_event.set()

        if self._runner:
            await self._runner.cleanup()

        self._runner = None
        self._site = None
        self._app = None
        self._adapter = None
        self._bot = None
        self._on_message = None
        self._stop_event = None
        logger.info("TeamsChannel stopped")
