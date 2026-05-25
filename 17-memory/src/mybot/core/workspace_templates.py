"""Substitute {{workspace}} path templates in agent and bootstrap text."""

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mybot.utils.config import Config


def substitute_workspace_templates(
    content: str,
    config: "Config",
    *,
    user_memories_root: Path | None = None,
) -> str:
    """Replace workspace path placeholders with resolved absolute paths."""
    user_root = (
        str(user_memories_root)
        if user_memories_root is not None
        else str(config.memories_path / "users")
    )
    return (
        content.replace("{{workspace}}", str(config.workspace))
        .replace("{{memories_path}}", str(config.memories_path))
        .replace("{{user_memories_path}}", user_root)
        .replace("{{crons_path}}", str(config.crons_path))
        .replace("{{agents_path}}", str(config.agents_path))
        .replace("{{skills_path}}", str(config.skills_path))
    )
