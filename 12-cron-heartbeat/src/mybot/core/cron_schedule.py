"""Helpers for detecting and creating scheduled cron jobs."""

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mybot.utils.config import Config

_SCHEDULE_REQUEST_RE = re.compile(
    r"\b("
    r"every\s+(morning|day|evening|night|week|hour|minute)|"
    r"daily|nightly|weekly|hourly|"
    r"each\s+morning|"
    r"send\s+me\b.*\b(every|daily|morning|schedule)|"
    r"schedule|scheduled|cron|remind\s+me|recurring|"
    r"at\s+\d{1,2}(:\d{2})?\s*(am|pm)?|"
    r"(?:after|in)\s+\d+\s*(?:min(?:ute)?s?)|"
    r"say\s+(?:hi|hello)\b.*\bafter\b"
    r")\b",
    re.IGNORECASE,
)

_MORNING_RE = re.compile(r"\bmorning\b", re.IGNORECASE)
_EVENING_RE = re.compile(r"\b(evening|night)\b", re.IGNORECASE)
_EVERY_MINUTE_RE = re.compile(r"every\s+(\d+)\s+minutes?", re.IGNORECASE)
_DELAY_MIN_RE = re.compile(
    r"(?:after|in)\s+(\d+)\s*(?:min(?:ute)?s?)",
    re.IGNORECASE,
)

# Minute hour day month weekday — one-shot at a specific time
_FIXED_TIME_CRON_RE = re.compile(
    r"^\d{1,2}\s+\d{1,2}\s+\d{1,2}\s+\d{1,2}\s+\*$"
)


def wants_schedule_request(message: str) -> bool:
    return bool(_SCHEDULE_REQUEST_RE.search(message))


def substitute_skill_templates(content: str, config: "Config") -> str:
    return content.replace("{{crons_path}}", str(config.crons_path)).replace(
        "{{workspace}}", str(config.workspace)
    )


def schedule_at_datetime(run_at: datetime) -> str:
    """Cron expression for a single run at the given local time (minute precision)."""
    return f"{run_at.minute} {run_at.hour} {run_at.day} {run_at.month} *"


def is_fixed_time_schedule(schedule: str) -> bool:
    return bool(_FIXED_TIME_CRON_RE.match(schedule.strip()))


def infer_schedule_and_one_off(message: str) -> tuple[str, bool]:
    """Return (cron_expression, one_off) from natural language."""
    delay_match = _DELAY_MIN_RE.search(message)
    if delay_match:
        minutes = max(1, int(delay_match.group(1)))
        run_at = datetime.now() + timedelta(minutes=minutes)
        run_at = run_at.replace(second=0, microsecond=0)
        now_minute = datetime.now().replace(second=0, microsecond=0)
        if run_at <= now_minute:
            run_at += timedelta(minutes=1)
        return schedule_at_datetime(run_at), True

    minute_match = _EVERY_MINUTE_RE.search(message)
    if minute_match:
        n = max(5, int(minute_match.group(1)))
        return f"*/{n} * * * *", False

    if _MORNING_RE.search(message):
        return "0 9 * * *", False
    if _EVENING_RE.search(message):
        return "0 18 * * *", False
    if re.search(r"\bweekly\b", message, re.IGNORECASE):
        return "0 9 * * 1", False
    if re.search(r"\bhourly\b", message, re.IGNORECASE):
        return "0 * * * *", False
    return "0 9 * * *", False


def infer_schedule(message: str) -> str:
    """Best-effort cron expression from natural language."""
    schedule, _ = infer_schedule_and_one_off(message)
    return schedule


def suggest_cron_id(message: str) -> str:
    if _DELAY_MIN_RE.search(message) and re.search(
        r"\b(hi|hello)\b", message, re.IGNORECASE
    ):
        return "hi-reminder"

    words = re.findall(r"[a-z0-9]+", message.lower())
    stop = {
        "send",
        "me",
        "some",
        "a",
        "an",
        "the",
        "every",
        "morning",
        "please",
        "can",
        "you",
        "i",
        "want",
        "to",
        "my",
        "after",
        "in",
        "mins",
        "min",
        "minutes",
        "minute",
        "say",
        "hi",
        "hello",
    }
    parts = [w for w in words if w not in stop][:4]
    slug = "-".join(parts) if parts else "scheduled-task"
    return slug[:48]


def cron_ops_system_addon(config: "Config", skill_content: str) -> str:
    return (
        "## Scheduled tasks (IMPORTANT)\n"
        "This bot supports recurring cron jobs stored under "
        f"`{config.crons_path}`.\n"
        "When the user asks for daily/weekly/recurring delivery or reminders:\n"
        "1. Call the `skill` tool with `skill_name` = `cron-ops` first if you have not already.\n"
        "2. Use `write` (and `bash` if needed) to create `<crons_path>/<cron-id>/CRON.md`.\n"
        "3. For delays like \"in 2 minutes\" or \"after 5 mins\", use a one-off cron at that "
        "exact minute (`one_off: true`) with schedule `M H D month *`.\n"
        "4. Do NOT say you cannot send scheduled messages — use cron + `post_message`.\n"
        "5. Confirm the cron id, schedule, and agent when done.\n\n"
        f"{skill_content}"
    )


def _yaml_quote(value: str) -> str:
    if re.search(r'[:#\n"\'\\]', value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def create_cron_job(
    config: "Config",
    *,
    cron_id: str,
    name: str,
    description: str,
    agent: str,
    schedule: str,
    prompt: str,
    one_off: bool = False,
) -> Path:
    """Write a CRON.md file and return its path."""
    cron_dir = config.crons_path / cron_id
    cron_dir.mkdir(parents=True, exist_ok=True)

    one_off_line = "one_off: true\n" if one_off else ""
    body = (
        "---\n"
        f"name: {_yaml_quote(name)}\n"
        f"description: {_yaml_quote(description)}\n"
        f"agent: {agent}\n"
        f'schedule: "{schedule}"\n'
        f"{one_off_line}"
        "---\n\n"
        f"{prompt.strip()}\n"
    )
    path = cron_dir / "CRON.md"
    path.write_text(body, encoding="utf-8")
    return path


def build_default_notify_prompt(user_request: str) -> str:
    if _DELAY_MIN_RE.search(user_request) and re.search(
        r"\b(hi|hello|greet)\b", user_request, re.IGNORECASE
    ):
        return (
            "Send the user a short, friendly greeting (e.g. Hi there!). "
            "Use the `post_message` tool — this is the only way to reach them."
        )

    return (
        f"Fulfill this scheduled task for the user: {user_request.strip()}\n\n"
        "Use websearch or webread if you need fresh content. "
        "When you have the result, use the `post_message` tool to send it to the user."
    )
