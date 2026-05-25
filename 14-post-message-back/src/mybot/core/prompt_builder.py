"""Prompt builder that assembles system prompt from layers."""

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mybot.core.context import SharedContext
    from mybot.core.events import EventSource
    from mybot.core.session_state import SessionState


class PromptBuilder:
    """Assembles system prompt from layered sources."""

    def __init__(self, context: "SharedContext"):
        self.context = context

    def build(self, state: "SessionState") -> str:
        """Build the full system prompt from layers."""
        layers = []

        # Layer 1: Identity
        layers.append(state.agent.agent_def.agent_md)

        # Layer 2: Soul (optional)
        if state.agent.agent_def.soul_md:
            layers.append(f"## Personality\n\n{state.agent.agent_def.soul_md}")

        # Layer 3: Bootstrap context
        bootstrap = self._load_bootstrap_context()
        if bootstrap:
            layers.append(bootstrap)

        # Layer 4: Skills (optional)
        skills_layer = self._build_skills_layer(state)
        if skills_layer:
            layers.append(skills_layer)

        # Layer 5: Scheduled tasks guidance
        layers.append(self._build_cron_guidance())

        # Layer 6: Runtime context
        layers.append(
            self._build_runtime_context(
                state.agent.agent_def.id,
                datetime.now(),
            )
        )

        # Layer 7: Channel hint
        layers.append(self._build_channel_hint(state.source))

        if state.ephemeral_system_addon:
            layers.append(state.ephemeral_system_addon)

        layers.append(
            "Always respond in plain natural language. "
            "Never output JSON, tool_calls, or other structured formats "
            "unless the user explicitly asks."
        )

        return "\n\n".join(layers)

    def _load_bootstrap_context(self) -> str:
        """Load BOOTSTRAP.md + AGENTS.md + cron list."""
        parts = []

        bootstrap_path = self.context.config.workspace / "BOOTSTRAP.md"
        if bootstrap_path.exists():
            parts.append(bootstrap_path.read_text(encoding="utf-8").strip())

        agents_path = self.context.config.workspace / "AGENTS.md"
        if agents_path.exists():
            parts.append(agents_path.read_text(encoding="utf-8").strip())

        cron_list = self._format_cron_list()
        if cron_list:
            parts.append(cron_list)

        return "\n\n".join(parts)

    def _format_cron_list(self) -> str:
        """Format crons as markdown list."""
        crons = self.context.cron_loader.discover_crons()
        if not crons:
            return ""

        lines = ["## Scheduled Tasks\n"]
        for cron in crons:
            lines.append(
                f"- **`{cron.id}`** — {cron.name}: {cron.description} "
                f"(`{cron.schedule}`)"
            )
        return "\n".join(lines)

    def _build_skills_layer(self, state: "SessionState") -> str:
        if not state.agent.agent_def.allow_skills:
            return ""

        skills = self.context.skill_loader.discover_skills()
        if not skills:
            return ""

        skill_lines = "\n".join(
            f"- **{skill.name}** (`{skill.id}`): {skill.description}"
            for skill in skills
        )
        return (
            "## Available Skills\n"
            f"{skill_lines}\n\n"
            "Use the `skill` tool to load a skill's full instructions when needed."
        )

    def _build_cron_guidance(self) -> str:
        crons_path = self.context.config.crons_path
        return (
            "## Scheduled tasks\n"
            f"You can create recurring jobs (cron) under `{crons_path}`. "
            "For daily memes, morning reminders, or recurring messages: "
            "load the `cron-ops` skill with the `skill` tool, then use `write` to create "
            f"`{crons_path}/<id>/CRON.md`. Never claim you cannot schedule messages."
        )

    def _build_runtime_context(self, agent_id: str, timestamp: datetime) -> str:
        """Build runtime info section."""
        return f"## Runtime\n\nAgent: {agent_id}\nTime: {timestamp.isoformat()}"

    def _build_channel_hint(self, source: "EventSource") -> str:
        """Build platform hint."""
        if source.is_cron:
            return (
                "You are running as a background cron job. "
                "Use the `post_message` tool exactly once to deliver results to the user. "
                "Do not call `post_message` again after it succeeds."
            )
        if source.is_agent:
            return (
                "You are running as a dispatched subagent. "
                "Your response will be sent to the main agent."
            )
        if source.is_platform:
            return f"You are responding via {source.platform_name}."
        return "You are responding via the CLI."
