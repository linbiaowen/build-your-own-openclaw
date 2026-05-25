"""Cron worker for scheduled job dispatch."""

import asyncio
import logging
import shutil
from datetime import datetime
from typing import TYPE_CHECKING

from croniter import croniter

from .worker import Worker
from mybot.core.agent import Agent
from mybot.core.events import CronEventSource, DispatchEvent

if TYPE_CHECKING:
    from mybot.core.cron_loader import CronDef
    from mybot.core.context import SharedContext

logger = logging.getLogger(__name__)


def find_due_jobs(
    jobs: list["CronDef"], now: datetime | None = None
) -> list["CronDef"]:
    """Find all jobs that are due to run."""
    if not jobs:
        return []

    now = now or datetime.now()
    now_minute = now.replace(second=0, microsecond=0)

    due_jobs = []
    for job in jobs:
        try:
            if croniter.match(job.schedule, now_minute):
                due_jobs.append(job)
        except Exception as e:
            logger.warning(f"Error checking schedule for {job.id}: {e}")
            continue

    return due_jobs


class CronWorker(Worker):
    """Finds due cron jobs, publishes DISPATCH events."""

    def __init__(self, context: "SharedContext"):
        super().__init__(context)
        self._fired_slots: set[str] = set()
        self._fired_minute: datetime | None = None

    async def run(self) -> None:
        """Check every minute for due jobs."""
        self.logger.info("CronWorker started")

        while True:
            try:
                await self._tick()
            except Exception as e:
                self.logger.error(f"Error in tick: {e}")

            jobs = self.context.cron_loader.discover_crons()
            interval = 15 if any(j.one_off for j in jobs) else 60
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        """Find and dispatch due jobs via EventBus."""
        now = datetime.now()
        now_minute = now.replace(second=0, microsecond=0)
        if self._fired_minute != now_minute:
            self._fired_slots.clear()
            self._fired_minute = now_minute

        jobs = self.context.cron_loader.discover_crons()
        due_jobs = find_due_jobs(jobs, now)

        for cron_def in due_jobs:
            slot = f"{cron_def.id}:{now_minute.isoformat()}"
            if slot in self._fired_slots:
                continue
            self._fired_slots.add(slot)

            if cron_def.one_off:
                cron_path = self.context.cron_loader.config.crons_path / cron_def.id
                if cron_path.exists():
                    shutil.rmtree(cron_path)
                    self.logger.info(
                        f"Removed one-off cron before dispatch: {cron_def.id}"
                    )

            agent_def = self.context.agent_loader.load(cron_def.agent)
            agent = Agent(agent_def, self.context)
            cron_source = CronEventSource(cron_id=cron_def.id)
            session = agent.new_session(cron_source)

            event = DispatchEvent(
                session_id=session.session_id,
                source=CronEventSource(cron_id=cron_def.id),
                content=cron_def.prompt,
            )
            await self.context.eventbus.publish(event)
            self.logger.info(f"Dispatched cron job: {cron_def.id}")
