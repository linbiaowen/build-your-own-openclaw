"""Agent definition loader."""

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from mybot.core.workspace_templates import substitute_workspace_templates
from mybot.utils.config import Config, LLMConfig
from mybot.utils.def_loader import (
    DefNotFoundError,
    InvalidDefError,
    discover_definitions,
    parse_definition,
)


class AgentDef(BaseModel):
    """Loaded agent definition with merged settings."""

    id: str
    name: str
    description: str = ""
    agent_md: str
    soul_md: str = ""  # Personality layer (optional)
    llm: LLMConfig
    allow_skills: bool = False
    max_concurrency: int = Field(default=1, ge=1)
    role: str | None = None  # e.g. "memory" — see agents/<id>/AGENT.md frontmatter


class AgentLoader:
    """Loads agent definitions from AGENT.md files."""

    @staticmethod
    def from_config(config: Config) -> "AgentLoader":
        return AgentLoader(config)

    def __init__(self, config: Config):
        """Initialize AgentLoader."""
        self.config = config

    def load(self, agent_id: str) -> AgentDef:
        """Load agent by ID."""
        agent_file = self.config.agents_path / agent_id / "AGENT.md"
        if not agent_file.exists():
            raise DefNotFoundError("agent", agent_id)

        try:
            content = agent_file.read_text()
            agent_def = parse_definition(content, agent_id, self._parse_agent_def)
        except InvalidDefError:
            raise
        except Exception as e:
            raise InvalidDefError("agent", agent_id, str(e))

        return agent_def

    def discover_agents(self) -> list[AgentDef]:
        """Scan agents directory and return list of valid AgentDef."""
        return discover_definitions(
            self.config.agents_path, "AGENT.md", self._parse_agent_def
        )

    def discover_by_role(self, role: str) -> list[AgentDef]:
        """Agents whose AGENT.md frontmatter sets role: <role>."""
        return [a for a in self.discover_agents() if a.role == role]

    def _parse_agent_def(
        self, def_id: str, frontmatter: dict[str, Any], body: str
    ) -> AgentDef:
        """Parse agent definition from frontmatter (callback for parse_definition)."""
        llm_overrides = frontmatter.get("llm")
        merged_llm = self._merge_llm_config(llm_overrides)

        # Load SOUL.md if exists
        soul_md = self._load_soul_md(def_id)

        body = substitute_workspace_templates(body.strip(), self.config)
        if soul_md:
            soul_md = substitute_workspace_templates(soul_md, self.config)

        try:
            return AgentDef(
                id=def_id,
                name=frontmatter["name"],  # type: ignore[misc]
                description=frontmatter.get("description", ""),
                agent_md=body,
                soul_md=soul_md,
                llm=merged_llm,
                allow_skills=frontmatter.get("allow_skills", False),
                max_concurrency=frontmatter.get("max_concurrency", 1),
                role=frontmatter.get("role"),
            )
        except ValidationError as e:
            raise InvalidDefError("agent", def_id, str(e))

    def _load_soul_md(self, agent_id: str) -> str:
        """Load SOUL.md file for an agent if it exists."""
        soul_path = self.config.agents_path / agent_id / "SOUL.md"
        if soul_path.exists():
            return soul_path.read_text().strip()
        return ""

    def _merge_llm_config(self, agent_llm: dict[str, Any] | None) -> LLMConfig:
        """Deep merge agent's llm config with global defaults."""
        base = self.config.llm.model_dump()
        if agent_llm:
            base = {**base, **agent_llm}
        return LLMConfig(**base)
