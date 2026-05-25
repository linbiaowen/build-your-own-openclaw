"""Configuration management with hot reload support."""

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

_CONFIG_WATCH_SUFFIXES = ("config.user.yaml", "config.runtime.yaml")


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str
    model: str
    api_key: str
    api_base: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)

    @field_validator("api_base")
    @classmethod
    def api_base_must_be_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("api_base must be a valid URL")
        return v


class TelegramConfig(BaseModel):
    """Telegram platform configuration."""

    enabled: bool = True
    bot_token: str
    allowed_user_ids: list[str] = Field(default_factory=list)


class DiscordConfig(BaseModel):
    """Discord platform configuration."""

    enabled: bool = True
    bot_token: str
    channel_id: str | None = None
    allowed_user_ids: list[str] = Field(default_factory=list)


class BraveWebSearchConfig(BaseModel):
    """Configuration for web search provider."""

    provider: Literal["brave"] = "brave"
    api_key: str


class Crawl4AIWebReadConfig(BaseModel):
    """Configuration for web read provider."""

    provider: Literal["crawl4ai"] = "crawl4ai"


class SourceSessionConfig(BaseModel):
    """Session affinity configuration for a source."""

    session_id: str


class ChannelConfig(BaseModel):
    """Channel configuration."""

    enabled: bool = False
    telegram: TelegramConfig | None = None
    discord: DiscordConfig | None = None


class ApiConfig(BaseModel):
    """HTTP API configuration."""

    host: str = "127.0.0.1"
    port: int = Field(default=8031, gt=0, lt=65536)


class Config(BaseModel):
    """Main configuration with hot reload support."""

    workspace: Path
    llm: LLMConfig
    default_agent: str
    agents_path: Path = Field(default=Path("agents"))
    skills_path: Path = Field(default=Path("skills"))
    crons_path: Path = Field(default=Path("crons"))
    logging_path: Path = Field(default=Path(".logs"))
    history_path: Path = Field(default=Path(".history"))
    event_path: Path = Field(default=Path(".event"))
    websearch: BraveWebSearchConfig | None = None
    webread: Crawl4AIWebReadConfig | None = None
    channels: ChannelConfig = Field(default_factory=ChannelConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    sources: dict[str, SourceSessionConfig] = Field(default_factory=dict)
    routing: dict = Field(default_factory=lambda: {"bindings": []})
    default_delivery_source: str | None = None

    @model_validator(mode="after")
    def resolve_paths(self) -> "Config":
        """Resolve relative paths to absolute using workspace."""
        for field_name in (
            "agents_path",
            "skills_path",
            "crons_path",
            "logging_path",
            "history_path",
            "event_path",
        ):
            path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, self.workspace / path)
        return self

    @classmethod
    def load(cls, workspace_dir: Path) -> "Config":
        """Load configuration from workspace directory."""
        config_data = cls._load_merged_configs(workspace_dir)
        config_data["workspace"] = workspace_dir
        return cls.model_validate(config_data)

    @classmethod
    def _load_merged_configs(cls, workspace_dir: Path) -> dict[str, Any]:
        """Load and merge user and runtime config files."""
        config_data: dict[str, Any] = {}

        user_config = workspace_dir / "config.user.yaml"
        runtime_config = workspace_dir / "config.runtime.yaml"

        if user_config.exists():
            with open(user_config, encoding="utf-8") as f:
                config_data = cls._deep_merge(config_data, yaml.safe_load(f) or {})

        if runtime_config.exists():
            with open(runtime_config, encoding="utf-8") as f:
                config_data = cls._deep_merge(config_data, yaml.safe_load(f) or {})

        return config_data

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Deep merge override dict into base dict."""
        result = base.copy()

        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = Config._deep_merge(result[key], value)
            else:
                result[key] = value

        return result

    def _set_nested(self, obj: dict, key: str, value: Any) -> None:
        """Set a nested value in a dict using dot notation."""
        keys = key.split(".")
        for k in keys[:-1]:
            if k not in obj or not isinstance(obj[k], dict):
                obj[k] = {}
            obj = obj[k]
        obj[keys[-1]] = value

    def _set_config_value(self, config_path: Path, key: str, value: Any) -> None:
        """Update a config value in a YAML file."""
        # Load existing or start fresh
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        if isinstance(value, BaseModel):
            value = value.model_dump()

        # Update the key (supports nested via dot notation)
        self._set_nested(data, key, value)

        # Write back
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

    def set_user(self, key: str, value: Any) -> None:
        """Update a config value in config.user.yaml."""
        self._set_config_value(self.workspace / "config.user.yaml", key, value)

    def set_runtime(self, key: str, value: Any) -> None:
        """Update a runtime value in config.runtime.yaml."""
        self._set_config_value(self.workspace / "config.runtime.yaml", key, value)

    def reload(self) -> bool:
        """Re-read config.user.yaml and merge with runtime."""
        before = (
            f"model={self.llm.model!r}, "
            f"temperature={self.llm.temperature}, "
            f"default_agent={self.default_agent!r}"
        )
        try:
            config_data = self._load_merged_configs(self.workspace)
            config_data["workspace"] = self.workspace

            new_config = Config.model_validate(config_data)

            for field_name in Config.model_fields:
                setattr(self, field_name, getattr(new_config, field_name))

            after = (
                f"model={self.llm.model!r}, "
                f"temperature={self.llm.temperature}, "
                f"default_agent={self.default_agent!r}"
            )
            if before == after:
                logger.info("Config hot reload OK (no effective changes) [%s]", before)
            else:
                logger.info("Config hot reload OK: %s -> %s", before, after)
            return True
        except Exception as e:
            logger.warning("Config hot reload FAILED: %s", e)
            return False


class ConfigHandler(FileSystemEventHandler):
    """Handles config file modification events."""

    def __init__(self, config: Config):
        self._config = config

    def on_modified(self, event) -> None:
        """Reload config when watched YAML files change."""
        if event.is_directory:
            return

        path = Path(event.src_path)
        if not any(path.name.endswith(suffix) for suffix in _CONFIG_WATCH_SUFFIXES):
            logger.debug("Ignoring filesystem event: %s", path.name)
            return

        logger.info("Config file changed: %s — reloading…", path.name)
        if not self._config.reload():
            logger.warning("Config reload did not apply after change to %s", path.name)


class ConfigReloader:
    """Manages watchdog observer for config hot reload."""

    def __init__(self, config: Config):
        self._config = config
        self._observer = Observer()

    def start(self) -> None:
        """Start watching config file for changes."""
        handler = ConfigHandler(self._config)
        workspace = self._config.workspace
        self._observer.schedule(handler, str(workspace), recursive=False)
        self._observer.start()
        watched = ", ".join(_CONFIG_WATCH_SUFFIXES)
        logger.info(
            "Config hot reload watcher started (workspace=%s, watching %s)",
            workspace,
            watched,
        )

    def stop(self) -> None:
        """Stop watching."""
        self._observer.stop()
        self._observer.join()
        del self._observer
        logger.info("Config hot reload watcher stopped")
