"""Cron job definition loader."""

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from mybot.utils.def_loader import DefNotFoundError, discover_definitions

if TYPE_CHECKING:
    from mybot.utils.config import Config

logger = logging.getLogger(__name__)


class CronDef(BaseModel):
    """Loaded cron job definition."""

    id: str
    name: str
    description: str
    agent: str
    schedule: str
    prompt: str
    one_off: bool = False


class CronLoader:
    """Load cron job definitions from CRON.md files."""

    @staticmethod
    def from_config(config: "Config") -> "CronLoader":
        return CronLoader(config)

    def __init__(self, config: "Config"):
        self.config = config
        self.config.crons_path.mkdir(parents=True, exist_ok=True)

    def discover_crons(self) -> list[CronDef]:
        """Scan crons directory and return list of valid CronDef."""
        return discover_definitions(
            self.config.crons_path, "CRON.md", self._parse_cron_def
        )

    def _parse_cron_def(
        self, def_id: str, frontmatter: dict[str, Any], body: str
    ) -> CronDef | None:
        try:
            return CronDef(
                id=def_id,
                name=frontmatter["name"],  # type: ignore[misc]
                description=frontmatter["description"],  # type: ignore[misc]
                agent=frontmatter["agent"],  # type: ignore[misc]
                schedule=frontmatter["schedule"],  # type: ignore[misc]
                prompt=body.strip(),
                one_off=frontmatter.get("one_off", False),
            )
        except ValidationError as e:
            logger.warning(f"Invalid cron '{def_id}': {e}")
            return None
        except KeyError as e:
            logger.warning(f"Missing required field in cron '{def_id}': {e}")
            return None

    def load(self, cron_id: str) -> CronDef:
        crons = self.discover_crons()
        for cron in crons:
            if cron.id == cron_id:
                return cron
        raise DefNotFoundError("cron", cron_id)
