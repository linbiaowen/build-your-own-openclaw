"""Built-in slash command handlers."""

import re
from typing import TYPE_CHECKING

from mybot.core.commands.base import Command
from mybot.utils.def_loader import DefNotFoundError

if TYPE_CHECKING:
    from mybot.core.agent import AgentSession


class SessionCommand(Command):
    """Show current session details."""

    name = "session"
    description = "Show current session details"

    async def execute(self, args: str, session: "AgentSession") -> str:
        info = session.shared_context.history_store.get_session_info(session.session_id)

        # Handle case where session not found in indexs
        created_str = info.created_at if info else "Unknown"

        lines = [
            f"**Session ID:** `{session.session_id}`",
            f"**Agent:** {session.agent.agent_def.name} (`{session.agent.agent_def.id}`)",
            f"**Created:** {created_str}",
            f"**Messages:** {len(session.state.messages)}",
            f"**Source:** `{session.source}`",
        ]
        return "\n".join(lines)


class HelpCommand(Command):
    """Show available commands."""

    name = "help"
    aliases = ["?"]
    description = "Show available commands"

    async def execute(self, args: str, session: "AgentSession") -> str:
        lines = ["**Available Commands:**"]
        for cmd in session.shared_context.command_registry.list_commands():
            names = [f"/{cmd.name}"] + [f"/{a}" for a in cmd.aliases]
            lines.append(f"{', '.join(names)} - {cmd.description}")
        return "\n".join(lines)


class CompactCommand(Command):
    """Trigger manual context compaction."""

    name = "compact"
    description = "Compact conversation context manually"

    async def execute(self, args: str, session: "AgentSession") -> str:
        # Force compaction regardless of threshold
        await session.context_guard._compact_messages(session.state)
        msg_count = len(session.state.messages)
        return f"✓ Context compacted. {msg_count} messages retained."


class ContextCommand(Command):
    """Show session context information."""

    name = "context"
    description = "Show session context information"

    async def execute(self, args: str, session: "AgentSession") -> str:
        token_count = session.context_guard.estimate_tokens(session.state)
        threshold = session.context_guard.token_threshold
        usage_pct = (token_count / threshold) * 100 if threshold > 0 else 0

        lines = [
            f"**Messages:** {len(session.state.messages)}",
            f"**Tokens:** {token_count:,} ({usage_pct:.1f}% of {threshold:,} threshold)",
        ]
        return "\n".join(lines)


class ClearCommand(Command):
    """Clear conversation and start fresh."""

    name = "clear"
    description = "Clear conversation and start fresh"

    async def execute(self, args: str, session: "AgentSession") -> str:
        source_str = str(session.source)

        session.shared_context.routing_table.config_source_session_cache(source_str, None)

        return "✓ Conversation cleared. Next message starts fresh."


class ListCronCommand(Command):
    """List all scheduled cron jobs."""

    name = "list-cron"
    aliases = ["crons", "list-crons"]
    description = "List all scheduled cron jobs"

    async def execute(self, args: str, session: "AgentSession") -> str:
        crons = session.shared_context.cron_loader.discover_crons()
        crons_path = session.shared_context.config.crons_path

        if not crons:
            return f"No cron jobs found in `{crons_path}`."

        lines = [f"**Cron jobs** (`{crons_path}`):\n"]
        for cron in sorted(crons, key=lambda c: c.id):
            one_off = "yes" if cron.one_off else "no"
            lines.append(
                f"- **`{cron.id}`** — {cron.name}\n"
                f"  - Schedule: `{cron.schedule}`\n"
                f"  - Agent: `{cron.agent}` | One-off: {one_off}\n"
                f"  - {cron.description}"
            )
        return "\n".join(lines)


class AgentCommand(Command):
    """List agents or show agent details."""

    name = "agent"
    aliases = ["agents"]
    description = "List agents or show agent details"

    async def execute(self, args: str, session: "AgentSession") -> str:
        if not args:
            # List agents
            agents = session.shared_context.agent_loader.discover_agents()
            lines = ["**Agents:**"]
            for agent in agents:
                marker = " (current)" if agent.id == session.agent.agent_def.id else ""
                lines.append(f"- `{agent.id}`: {agent.description}{marker}")
            return "\n".join(lines)

        # Show specific agent details
        agent_id = args.strip()
        try:
            agent_def = session.shared_context.agent_loader.load(agent_id)
        except ValueError:
            return f"✗ Agent `{agent_id}` not found."

        lines = [
            f"**Agent:** `{agent_def.id}`",
            f"**Name:** {agent_def.name}",
            f"**Description:** {agent_def.description}",
            f"**LLM:** {agent_def.llm.model}",
        ]

        # Add content sections
        lines.append(f"\n---\n\n**AGENT.md:**\n```\n{agent_def.agent_md}\n```")

        if agent_def.soul_md:
            lines.append(f"\n**SOUL.md:**\n```\n{agent_def.soul_md}\n```")

        return "\n".join(lines)


class SkillsCommand(Command):
    """List all skills or show skill details."""

    name = "skills"
    description = "List all skills or show skill details"

    async def execute(self, args: str, session: "AgentSession") -> str:
        if not args:
            skills = session.shared_context.skill_loader.discover_skills()
            if not skills:
                return "No skills configured."

            lines = ["**Skills:**"]
            for skill in skills:
                lines.append(f"- `{skill.id}`: {skill.description}")
            return "\n".join(lines)

        # Show specific skill details
        skill_id = args.strip()
        try:
            skill = session.shared_context.skill_loader.load_skill(skill_id)
        except DefNotFoundError:
            return f"✗ Skill `{skill_id}` not found."

        lines = [
            f"**Skill:** `{skill.id}`",
            f"**Name:** {skill.name}",
            f"**Description:** {skill.description}",
            f"\n---\n\n**SKILL.md:**\n```\n{skill.content}\n```",
        ]
        return "\n".join(lines)


class RouteCommand(Command):
    """Create a routing binding."""

    name = "route"
    description = "Create a routing binding (persists to config)"

    async def execute(self, args: str, session: "AgentSession") -> str:
        parts = args.strip().split(None, 1)
        if len(parts) != 2:
            return "**Usage:** `/route <source_pattern> <agent_id>`\n\nExample: `/route platform-telegram:.* pickle`"

        pattern, agent_id = parts

        # Validate regex pattern
        try:
            re.compile(f"^{pattern}$")
        except re.error as e:
            return f"✗ Invalid regex pattern: {e}"

        # Verify agent exists
        try:
            session.shared_context.agent_loader.load(agent_id)
        except ValueError:
            return f"✗ Agent `{agent_id}` not found."

        # Create and persist binding
        session.shared_context.routing_table.persist_binding(pattern, agent_id)

        return f"✓ Route bound: `{pattern}` → `{agent_id}`"


_CLEAR_ALL_FLAGS = frozenset({"--all", "-all", "all", "*"})


def _normalize_route_token(token: str) -> str:
    """Normalize dashes/whitespace so `--all` flags match reliably."""
    cleaned = "".join(
        ch for ch in token if ord(ch) >= 32 and ch not in "\u200b\u200c\u200d\ufeff"
    )
    return (
        cleaned.strip()
        .lower()
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )


def _unroute_clear_all(args: str) -> bool:
    """True when the user asked to remove every binding."""
    stripped = args.strip()
    if not stripped:
        return False
    first = _normalize_route_token(stripped.split(None, 1)[0])
    return first in _CLEAR_ALL_FLAGS


class UnrouteCommand(Command):
    """Remove routing binding(s)."""

    name = "unroute"
    aliases = ["del-route", "remove-route"]
    description = "Remove routing binding(s) (persists to config)"

    async def execute(self, args: str, session: "AgentSession") -> str:
        args = args.strip()
        routing_table = session.shared_context.routing_table

        if not args:
            return (
                "**Usage:**\n"
                "- `/unroute <source_pattern>` — remove all bindings for that pattern\n"
                "- `/unroute <source_pattern> <agent_id>` — remove one exact binding\n"
                "- `/unroute --all` — remove every binding (also: `/unroute *`)"
            )

        if _unroute_clear_all(args):
            removed = routing_table.remove_bindings()
            if removed == 0:
                return "No routing bindings to remove."
            return f"✓ Removed all {removed} routing binding(s)."

        parts = args.split(None, 1)
        pattern = parts[0]
        agent_id = parts[1] if len(parts) > 1 else None

        try:
            re.compile(f"^{pattern}$")
        except re.error as e:
            return f"✗ Invalid regex pattern: {e}"

        if agent_id:
            try:
                session.shared_context.agent_loader.load(agent_id)
            except ValueError:
                return f"✗ Agent `{agent_id}` not found."

        removed = routing_table.remove_bindings(pattern, agent_id)
        if removed == 0:
            if agent_id:
                return f"✗ No binding found for `{pattern}` → `{agent_id}`."
            return f"✗ No binding found for pattern `{pattern}`."

        if agent_id:
            return f"✓ Removed {removed} binding(s): `{pattern}` → `{agent_id}`."
        return f"✓ Removed {removed} binding(s) matching `{pattern}`."


class BindingsCommand(Command):
    """Show all routing bindings."""

    name = "bindings"
    description = "Show all routing bindings"

    async def execute(self, args: str, session: "AgentSession") -> str:
        bindings = session.shared_context.config.routing.get("bindings", [])

        if not bindings:
            return "No routing bindings configured."

        lines = ["**Routing Bindings:**"]
        for binding in bindings:
            lines.append(f"- `{binding['value']}` → `{binding['agent']}`")

        return "\n".join(lines)
