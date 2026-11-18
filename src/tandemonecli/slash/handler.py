"""Slash command handler for model selection workflow."""

from dataclasses import dataclass, field
from typing import Iterable, Optional

from tandemonecli.config.defaults import Defaults, DefaultsError, save_defaults
from tandemonecli.startup.models import AvailableModels
from tandemonecli.text import safe_markup

# How commands are shown in help and hints: the command itself, and what you fill in
COMMAND_STYLE = "bold cyan"
ARGUMENT_STYLE = "bold magenta"


def command_markup(usage: str) -> str:
    """Show a command's usage, such as ``/model <name>``, in colour.

    The command is cyan and what you fill in is magenta, so it's clear which
    part you type as is.
    """
    name, _, arguments = usage.partition(" ")
    markup = f"[{COMMAND_STYLE}]{safe_markup(name)}[/{COMMAND_STYLE}]"
    if arguments:
        markup += f" [{ARGUMENT_STYLE}]{safe_markup(arguments)}[/{ARGUMENT_STYLE}]"
    return markup


def bullet_list(items: Iterable[str]) -> str:
    """Format items as an indented bullet list, one per line."""
    return "\n".join(f"  • {safe_markup(item)}" for item in items)


def selection(defaults: Defaults) -> str:
    """The provider and model of ``defaults``, in bold, as "provider / model"."""
    names = " / ".join(
        safe_markup(name) for name in (defaults.provider, defaults.model) if name
    )
    return f"[bold]{names}[/bold]"


def describe_defaults(defaults: Defaults) -> str:
    """Summarise ``defaults`` on one line, for messages."""
    model = selection(defaults) if defaults.provider else "no model"
    return f"{model}, web search {'on' if defaults.web else 'off'}"


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
    CommandInfo("/web", "Toggle web search for the model on or off"),
    CommandInfo(
        "/max-tokens <tokens>",
        "Limit how many tokens the model may reply with; no number resets it",
    ),
    CommandInfo(
        "/set-default",
        "Start with the current provider, model and web search",
    ),
)

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
    thread_id: Optional[str] = None
    # Whether the model may search the web; shown under the input when on
    web: bool = False
    # Most tokens the model may generate per reply; None leaves it to the model
    max_output_tokens: Optional[int] = None


@dataclass
class SlashHandler:
    """Handles slash commands for provider/model selection."""

    models: AvailableModels
    state: SlashState = field(default_factory=SlashState)

    def get_help(self) -> str:
        """Return a one-line summary of the commands."""
        usages = ", ".join(command_markup(c.usage) for c in COMMANDS)
        return f"Commands: {usages}. Type {command_markup('/help')} for details."

    def get_detailed_help(self) -> str:
        """Return every command with a description of what it does."""
        commands = (*COMMANDS, EXIT_COMMAND)
        width = max(len(c.usage) for c in commands)
        lines = [
            f"  {command_markup(c.usage)}{' ' * (width - len(c.usage))}"
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

        elif command == "web":
            return self._handle_web_search()

        elif command == "max-tokens":
            return self._handle_max_tokens(arg)

        elif command == "set-default":
            return self._handle_set_default()

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
            f"Select one with {command_markup('/provider <name>')}"
        )

    def apply_defaults(self, defaults: Defaults) -> Optional[str]:
        """Start with the saved defaults, and say what was applied, if anything.

        The provider and model are selected only if that model is available now;
        otherwise neither is, so you never start with half a selection.
        """
        if defaults == Defaults():
            return None

        self.state.web = defaults.web
        message = f"Using your defaults: {describe_defaults(defaults)}"
        if defaults.provider and defaults.model:
            if defaults.model in self.models.providers.get(defaults.provider, []):
                self.state.selected_provider = defaults.provider
                self.state.selected_model = defaults.model
            else:
                message += (
                    f"\n[yellow]Your default model {selection(defaults)} isn't "
                    "available now, so select one with /provider and /model.[/yellow]"
                )
        return message

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
                f"Select one with {command_markup('/model <name>')}"
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

    def _handle_web_search(self) -> CommandResponse:
        """Handle /web command: toggle web search on or off."""
        self.state.web = not self.state.web
        if self.state.web:
            return CommandResponse("[bold green]✓ Web search enabled[/bold green]")
        return CommandResponse("[dim]Web search disabled[/dim]")

    def _handle_max_tokens(self, arg: Optional[str]) -> CommandResponse:
        """Handle /max-tokens command: set or reset the max output tokens."""
        if arg is None:
            self.state.max_output_tokens = None
            return CommandResponse(
                "[dim]Max output tokens reset to the model default[/dim]"
            )

        try:
            tokens = int(arg)
        except ValueError:
            tokens = 0
        if tokens < 1:
            return CommandResponse(
                f"[red]Error: '{safe_markup(arg)}' is not a whole number of at least 1.[/red]",
                message_type="error",
            )

        self.state.max_output_tokens = tokens
        return CommandResponse(f"Max output tokens: [bold]{tokens}[/bold]")

    def _handle_set_default(self) -> CommandResponse:
        """Handle /set-default command: save the current state as the defaults."""
        if not (self.state.selected_provider and self.state.selected_model):
            return CommandResponse(
                "[red]Error: Select a provider and model first, so there's "
                "something to start with.[/red]",
                message_type="error",
            )

        defaults = Defaults(
            provider=self.state.selected_provider,
            model=self.state.selected_model,
            web=self.state.web,
        )
        try:
            save_defaults(defaults)
        except DefaultsError as e:
            return CommandResponse(
                f"[red]Couldn't save the defaults: {safe_markup(str(e))}[/red]",
                message_type="error",
            )
        return CommandResponse(
            f"[green]Saved as your defaults:[/green] {describe_defaults(defaults)}"
        )
