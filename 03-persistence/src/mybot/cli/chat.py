"""Chat CLI command for interactive sessions with persistence."""

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from mybot.core.agent import Agent
from mybot.core.agent_loader import AgentLoader
from mybot.utils.config import Config


class ChatLoop:
    """Interactive chat session with persistence."""

    def __init__(
        self,
        config: Config,
        agent_id: str | None = None,
        *,
        new_session: bool = False,
        session_id: str | None = None,
    ):
        self.config = config
        self.console = Console()

        loader = AgentLoader(config)
        agent_id = agent_id or config.default_agent
        self.agent_def = loader.load(agent_id)

        self.agent = Agent(self.agent_def, config)

        if session_id:
            self.session = self.agent.load_session(session_id)
            self.agent.history_store.set_active_session(
                self.agent_def.id, session_id
            )
            info = self.agent.history_store.get_session_info(session_id)
            self._resume_label = info.title if info else session_id[:8]
        elif new_session:
            self.session = self.agent.new_session()
            self._resume_label = None
        else:
            resumed = self.agent.resume_active_session()
            if resumed:
                self.session = resumed
                info = self.agent.history_store.get_session_info(self.session.session_id)
                self._resume_label = info.title if info else self.session.session_id[:8]
            else:
                self.session = self.agent.new_session()
                self._resume_label = None

    def get_user_input(self) -> str:
        """Get user input with styled prompt."""
        prompt_text = Text("You", style="cyan")
        user_input = Prompt.ask(prompt_text, console=self.console)
        return user_input.strip()

    def display_agent_response(self, content: str) -> None:
        """Display agent response with styled prefix."""
        prefix = Text(f"{self.agent_def.id}: ", style="green")

        self.console.print(prefix, end="")
        self.console.print(content)

    async def run(self) -> None:
        """Run the interactive chat loop."""
        welcome = "Welcome to my-bot!"
        if self._resume_label:
            welcome += f"\nResuming session: {self._resume_label}"

        self.console.print(
            Panel(
                Text(welcome, style="bold cyan"),
                title="Chat",
                border_style="cyan",
            )
        )
        self.console.print(
            "Type 'quit' or 'exit' to end the session.\n"
            "[dim]Use --new to start a fresh session.[/dim]\n"
        )

        try:
            while True:
                user_input = await asyncio.to_thread(self.get_user_input)

                if user_input.lower() in ("quit", "exit", "q"):
                    self.console.print("\n[bold yellow]Goodbye![/bold yellow]")
                    break

                if not user_input:
                    continue

                try:
                    response = await self.session.chat(user_input)
                    self.display_agent_response(response)
                except Exception as e:
                    self.console.print(f"\n[bold red]Error:[/bold red] {e}\n")

        except (KeyboardInterrupt, EOFError):
            self.console.print("\n[bold yellow]Goodbye![/bold yellow]")


def chat_command(
    ctx: typer.Context,
    agent_id: str | None = None,
    *,
    new_session: bool = False,
    session_id: str | None = None,
) -> None:
    """Start interactive chat session."""
    config = ctx.obj.get("config")

    chat_loop = ChatLoop(
        config,
        agent_id=agent_id,
        new_session=new_session,
        session_id=session_id,
    )
    asyncio.run(chat_loop.run())
