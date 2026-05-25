"""Prompt builder that assembles system prompt from layers."""

from datetime import datetime
from typing import TYPE_CHECKING

from mybot.core.memory_paths import (
    end_user_id_for_session,
    load_identity_snippet,
    memory_scope_prompt,
)
from mybot.core.workspace_templates import substitute_workspace_templates

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

        dispatch_layer = self._build_dispatch_guidance(state)
        if dispatch_layer:
            layers.append(dispatch_layer)

        memory_layer = self._build_memory_scope_layer(state)
        if memory_layer:
            layers.append(memory_layer)

        identity_layer = self._build_known_identity_layer(state)
        if identity_layer:
            layers.append(identity_layer)

        # Layer 6: Runtime context
        layers.append(
            self._build_runtime_context(
                state.agent.agent_def.id,
                datetime.now(),
                state,
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
            parts.append(
                substitute_workspace_templates(
                    bootstrap_path.read_text(encoding="utf-8").strip(),
                    self.context.config,
                )
            )

        agents_path = self.context.config.workspace / "AGENTS.md"
        if agents_path.exists():
            parts.append(
                substitute_workspace_templates(
                    agents_path.read_text(encoding="utf-8").strip(),
                    self.context.config,
                )
            )

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

    def _build_memory_scope_layer(self, state: "SessionState") -> str:
        """Tell agents where this end-user's memories live (platform sessions)."""
        if state.source.is_cron:
            return ""

        user_id = end_user_id_for_session(state)
        if not user_id:
            return ""

        scope = memory_scope_prompt(self.context.config, user_id)

        memory_agents = self.context.agent_loader.discover_by_role("memory")
        current = state.agent.agent_def
        if memory_agents and current.role != "memory":
            delegate_ids = ", ".join(f"`{a.id}`" for a in memory_agents)
            scope += (
                f"\n\nFor **storing** new facts, use `subagent_dispatch` to "
                f"{delegate_ids}. "
                "For **what is my name** / identity recall, use the "
                "`Known identity` section below — answer directly, do not dispatch."
            )
        return scope

    def _build_known_identity_layer(self, state: "SessionState") -> str:
        """Inject on-disk identity.md so name recall works without subagent hops."""
        if state.source.is_cron:
            return ""

        user_id = end_user_id_for_session(state)
        if not user_id:
            return ""

        snippet = load_identity_snippet(self.context.config, user_id)
        if not snippet:
            return ""

        return (
            "## Known identity (this end-user)\n"
            f"{snippet}\n\n"
            "When the user asks their name or who they are, answer from this block "
            "in plain language. Do not call `subagent_dispatch` for simple name recall."
        )

    def _build_dispatch_guidance(self, state: "SessionState") -> str:
        if state.source.is_cron:
            return ""

        agents = self.context.agent_loader.discover_agents()
        others = [a for a in agents if a.id != state.agent.agent_def.id]
        if not others:
            return ""

        agent_lines = "\n".join(
            f"- `{a.id}`: {a.description}" for a in others
        )
        return (
            "## Agent dispatch\n"
            "You can delegate specialized work to other agents using the "
            "`subagent_dispatch` tool. Use it when another agent is a better fit; "
            "summarize results for the user when the subagent returns.\n"
            f"{agent_lines}"
        )

    def _build_runtime_context(
        self, agent_id: str, timestamp: datetime, state: "SessionState"
    ) -> str:
        """Build runtime info section."""
        cfg = self.context.config
        lines = [
            f"Agent: {agent_id}",
            f"Time: {timestamp.isoformat()}",
            f"Workspace: {cfg.workspace}",
            f"Memories base: {cfg.memories_path}",
        ]
        user_id = end_user_id_for_session(state)
        if user_id:
            from mybot.core.memory_paths import user_memories_path

            lines.append(
                f"This end-user's memories: {user_memories_path(cfg, user_id)}"
            )
        return "## Runtime\n\n" + "\n".join(lines)

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
