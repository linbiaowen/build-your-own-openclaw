from mybot.core.agent_loader import AgentLoader
from mybot.core.commands.registry import CommandRegistry
from mybot.core.cron_loader import CronLoader
from mybot.core.history import HistoryStore
from mybot.core.skill_loader import SkillLoader
from mybot.core.eventbus import EventBus
from mybot.utils.config import Config


class SharedContext:
    """Global shared state for the application."""

    config: Config
    history_store: HistoryStore
    agent_loader: AgentLoader
    skill_loader: SkillLoader
    cron_loader: CronLoader
    command_registry: CommandRegistry
    eventbus: EventBus

    def __init__(self, config: Config) -> None:
        self.config = config
        self.history_store = HistoryStore.from_config(config)
        self.agent_loader = AgentLoader.from_config(config)
        self.skill_loader = SkillLoader.from_config(config)
        self.cron_loader = CronLoader.from_config(config)
        self.command_registry = CommandRegistry.with_builtins()
        self.eventbus = EventBus(self)
