"""Slash command handler for model selection workflow."""

from dataclasses import dataclass, field
from typing import Iterable, Optional

from tandemonecli.startup.models import AvailableModels
from tandemonecli.text import safe_markup


def bullet_list(items: Iterable[str]) -> str:
    """Format items as an indented bullet list, one per line."""
    return "\n".join(f"  • {safe_markup(item)}" for item in items)


@dataclass(frozen=True)
class CommandInfo:
    """Usage and description of a command, shown in the help text."""

    usage: str
    description: str


COMMANDS: tuple[CommandInfo, ...] = (
    CommandInfo("/provider <name>", "Select a provider and list its models"),
    CommandInfo("/model <name>", "Select a model from the selected provider"),
    CommandInfo("/new", "Start a new conversation; the model forgets earlier messages"),
    CommandInfo("/clear", "Clear the selected provider and model"),
    CommandInfo("/help", "Show this help"),
)

# Not a slash command, but listed in the detailed help so it's easy to find
EXIT_COMMAND = CommandInfo("exit", "Quit Tandem One CLI (or press Ctrl+C)")


@dataclass
class CommandResponse:
    """Response from a command execution."""

    message: str
    message_type: str = "normal"


@dataclass
class SlashState:
    """State for slash command handler."""

    selected_provider: Optional[str] = None
    selected_model: Optional[str] = None
    # ID of the current conversation; None means the next message starts a new one
    thread_id: Optional[str] = None


@dataclass
class SlashHandler:
    """Handles slash commands for provider/model selection."""

    models: AvailableModels
    state: SlashState = field(default_factory=SlashState)

    def get_help(self) -> str:
        """Return a one-line summary of the commands."""
        usages = ", ".join(f"[bold]{safe_markup(c.usage)}[/bold]" for c in COMMANDS)
        return f"Commands: {usages}. Type [bold]/help[/bold] for details."

    def get_detailed_help(self) -> str:
        """Return every command with a description of what it does."""
        commands = (*COMMANDS, EXIT_COMMAND)
        width = max(len(c.usage) for c in commands)
        lines = [
            f"  [bold]{safe_markup(c.usage)}[/bold]{' ' * (width - len(c.usage))}"
            f"   {safe_markup(c.description)}"
            for c in commands
        ]
        return "[bold]Available commands:[/bold]\n" + "\n".join(lines)

    def handle(self, user_input: str) -> Optional[CommandResponse]:
        """Parse and handle a slash command. Returns None for non-commands."""

        if not user_input.startswith("/"):
            return None

        parts = user_input[1:].split(maxsplit=1)
        if not parts:
            return CommandResponse(
                f"[red]Error: No command given.[/red] {self.get_help()}",
                message_type="error",
            )

        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if command == "provider":
            return self._handle_provider(arg)

        elif command == "model":
            return self._handle_model(arg)

        elif command == "new":
            return self._handle_new()

        elif command == "clear":
            return self._handle_clear()

        elif command == "help":
            return self._handle_help()

        else:
            return CommandResponse(
                f"[red]Unknown command: /{command}[/red]\n{self.get_help()}",
                message_type="error",
            )

    def get_providers_overview(self) -> str:
        """Return the list of available providers, shown at startup."""
        if not self.models.providers:
            return "[red]No providers with chat models are available.[/red]"
        return (
            "[bold]Available providers:[/bold]\n"
            f"{bullet_list(self.models.providers)}\n\n"
            "Select one with [bold]/provider <name>[/bold]"
        )

    def get_context_prefix(self) -> str:
        """Return a prefix string based on current selection."""
        if self.state.selected_model:
            return (
                f"[Using {self.state.selected_provider}/{self.state.selected_model}] "
            )
        elif self.state.selected_provider:
            return f"[Using {self.state.selected_provider}] "
        return ""

    def _handle_provider(self, arg: Optional[str]) -> CommandResponse:
        """Handle /provider command."""
        if arg and arg in self.models.providers:
            self.state.selected_provider = arg
            self.state.selected_model = None
            return CommandResponse(
                f"Selected provider: [bold]{safe_markup(arg)}[/bold]\n\n"
                "[bold]Available models:[/bold]\n"
                f"{bullet_list(self.models.providers[arg])}\n\n"
                "Select one with [bold]/model <name>[/bold]"
            )
        else:
            available = ", ".join(self.models.providers.keys())
            return CommandResponse(
                f"[red]Error: Provider '{arg}' not found. Available: {available}[/red]",
                message_type="error",
            )

    def _handle_model(self, arg: Optional[str]) -> CommandResponse:
        """Handle /model command."""
        if not self.state.selected_provider:
            return CommandResponse(
                "[red]Error: Please select a provider first with /provider <name>[/red]",
                message_type="error",
            )

        if arg and arg in self.models.providers[self.state.selected_provider]:
            self.state.selected_model = arg
            return CommandResponse(
                f"Selected model: [bold]{arg}[/bold] from provider [bold]{self.state.selected_provider}[/bold]"
            )
        else:
            available = ", ".join(self.models.providers[self.state.selected_provider])
            return CommandResponse(
                f"[red]Error: Model '{arg}' not found for {self.state.selected_provider}. Available: {available}[/red]",
                message_type="error",
            )

    def _handle_new(self) -> CommandResponse:
        """Handle /new command."""
        self.state.thread_id = None
        return CommandResponse("Started a new conversation.")

    def _handle_clear(self) -> CommandResponse:
        """Handle /clear command."""
        self.state.selected_provider = None
        self.state.selected_model = None
        return CommandResponse("Selection cleared.")

    def _handle_help(self) -> CommandResponse:
        """Handle /help command."""
        return CommandResponse(self.get_detailed_help())
