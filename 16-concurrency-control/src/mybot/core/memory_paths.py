"""Per end-user memory directory helpers."""

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mybot.core.events import EventSource
    from mybot.core.session_state import SessionState
    from mybot.utils.config import Config

_USER_ID_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_NAME_RECALL_RE = re.compile(
    r"\b(what(?:'s| is)|whats)\s+my\s+name\b"
    r"|\bwho\s+am\s+i\b"
    r"|\btell\s+me\s+my\s+name\b"
    r"|\bdo\s+you\s+(?:know|remember)\s+my\s+name\b",
    re.IGNORECASE,
)
_NAME_STORE_RE = re.compile(
    r"\b(?:remember|save)\s+(?:that\s+)?my\s+name\b"
    r"|\bmy\s+name\s+is\b",
    re.IGNORECASE,
)
_NAME_LINE_RE = re.compile(r"^[-*]?\s*name\s*:\s*(.+)$", re.IGNORECASE)


def sanitize_user_id(user_id: str) -> str:
    """Filesystem-safe segment for memories/users/<id>/."""
    cleaned = _USER_ID_SAFE_RE.sub("_", user_id.strip())
    return cleaned or "unknown"


def end_user_id_for_session(state: "SessionState") -> str | None:
    """End-user id for memory scoping (platform source or subagent parent)."""
    from mybot.core.session_state import SessionState

    if not isinstance(state, SessionState):
        return None
    if state.end_user_id:
        return state.end_user_id
    return end_user_id_from_source(state.source)


def end_user_id_from_source(source: "EventSource") -> str | None:
    """Stable id for the human end-user behind a platform session."""
    if source.is_platform:
        uid = getattr(source, "user_id", None)
        if uid is not None:
            return str(uid)
        if str(source).startswith("platform-cli:"):
            return "cli-user"
    return None


def user_memories_path(config: "Config", user_id: str) -> Path:
    """Root directory for one end-user's memories."""
    return config.memories_path / "users" / sanitize_user_id(user_id)


def ensure_user_memories_dir(config: "Config", user_id: str) -> Path:
    """Create and return the per-user memories root."""
    path = user_memories_path(config, user_id)
    for sub in ("topics", "projects", "daily-notes"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def memory_scope_prompt(config: "Config", user_id: str) -> str:
    """System-prompt block scoping read/write to one user's tree."""
    root = ensure_user_memories_dir(config, user_id)
    return (
        "## Memory scope (this end-user only)\n"
        f"End-user id: `{user_id}`\n"
        f"Memories root — store and read **only** under:\n`{root}`\n\n"
        "Use these subfolders under that root:\n"
        f"- `{root}/topics/` — timeless facts (e.g. identity.md)\n"
        f"- `{root}/projects/` — project notes\n"
        f"- `{root}/daily-notes/` — YYYY-MM-DD.md\n\n"
        "Never write to another user's directory or a shared `topics/identity.md` "
        "at the workspace memories root."
    )


def identity_file_path(config: "Config", user_id: str) -> Path:
    """Per-user topics/identity.md path."""
    return user_memories_path(config, user_id) / "topics" / "identity.md"


def parse_display_name_from_identity(text: str) -> str | None:
    """Extract a display name from identity.md body."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _NAME_LINE_RE.match(line)
        if match:
            name = match.group(1).strip()
            if name:
                return name
    return None


def wants_name_recall(message: str) -> bool:
    """True when the user asks to recall their name (not store it)."""
    if _NAME_STORE_RE.search(message) and not _NAME_RECALL_RE.search(message):
        return False
    return bool(_NAME_RECALL_RE.search(message))


def identity_recall_reply(state: "SessionState") -> str | None:
    """Deterministic answer for name-recall questions from on-disk identity."""
    user_id = end_user_id_for_session(state)
    if not user_id:
        return None

    config = state.shared_context.config
    path = identity_file_path(config, user_id)
    if not path.exists():
        return (
            "I don't have your name saved yet. "
            "Tell me what you'd like me to call you."
        )

    name = parse_display_name_from_identity(path.read_text(encoding="utf-8"))
    if name:
        return f"Your name is {name}."
    return (
        "I have a profile for you but no name recorded yet. "
        "What should I call you?"
    )


def load_identity_snippet(config: "Config", user_id: str, max_chars: int = 1200) -> str:
    """Short identity.md excerpt for the system prompt."""
    path = identity_file_path(config, user_id)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…"
    return text
