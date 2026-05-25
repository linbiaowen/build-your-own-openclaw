"""Built-in tools for agent capabilities."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from mybot.tools.base import tool

if TYPE_CHECKING:
    from mybot.core.agent import AgentSession


# Filesystem tools


@tool(
    name="read",
    description="Read the contents of a text file",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read"},
        },
        "required": ["path"],
    },
)
async def read_file(path: str, session: "AgentSession") -> str:
    """Read and return the contents of a file at the given path."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except PermissionError:
        return f"Error: Permission denied reading: {path}"
    except IsADirectoryError:
        return f"Error: Path is a directory, not a file: {path}"
    except Exception as e:
        return f"Error reading file: {e}"


@tool(
    name="write",
    description="Write content to a file",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {
                "type": "string",
                "description": "Content to write to the file",
            },
        },
        "required": ["path", "content"],
    },
)
async def write_file(path: str, content: str, session: "AgentSession") -> str:
    """Write content to a file at the given path."""
    try:
        Path(path).write_text(content, encoding="utf-8")
        return f"Successfully wrote to: {path}"
    except PermissionError:
        return f"Error: Permission denied writing to: {path}"
    except IsADirectoryError:
        return f"Error: Path is a directory, not a file: {path}"
    except Exception as e:
        return f"Error writing file: {e}"


@tool(
    name="edit",
    description="Edit a file by replacing a string with new content",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to edit"},
            "old_text": {"type": "string", "description": "The text to replace"},
            "new_text": {
                "type": "string",
                "description": "The new text to replace with",
            },
        },
        "required": ["path", "old_text", "new_text"],
    },
)
async def edit_file(
    path: str, old_text: str, new_text: str, session: "AgentSession"
) -> str:
    """Edit a file by replacing old_text with new_text."""
    try:
        content = Path(path).read_text(encoding="utf-8")
        if old_text not in content:
            return f"Error: '{old_text}' not found in {path}"
        new_content = content.replace(old_text, new_text)
        Path(path).write_text(new_content, encoding="utf-8")
        return f"Successfully edited {path}"
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except PermissionError:
        return f"Error: Permission denied editing: {path}"
    except Exception as e:
        return f"Error editing file: {e}"


# Shell tool


@tool(
    name="bash",
    description="Execute a bash shell command",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to execute"},
        },
        "required": ["command"],
    },
)
async def bash(command: str, session: "AgentSession") -> str:
    """Execute a bash command and return the output."""
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return "Error: command timed out after 30s"
        output = stdout.decode() if stdout else ""
        error = stderr.decode() if stderr else ""
        if output and error:
            return f"{output}\n{error}"
        return output or error or "Command completed with no output"
    except Exception as e:
        return f"Error executing command: {e}"
