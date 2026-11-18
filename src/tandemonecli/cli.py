from functools import cache

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import has_completions, is_done
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import (
    BufferControl,
    ConditionalContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.containers import WindowRenderInfo
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style
from rich.console import Console, Group
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

from tandemonecli.config.defaults import DefaultsError, load_defaults
from tandemonecli.config.settings import tocli_settings
from tandemonecli.permissions import (
    PermissionsError,
    allow_in_project,
    is_allowed,
    load_permissions,
    suggest_rule,
)
from tandemonecli.slash.completion import SlashCompleter
from tandemonecli.slash.handler import SlashHandler, SlashState, command_markup
from tandemonecli.startup.models import AvailableModels, get_models
from tandemonecli.startup.user import get_user
from tandemonecli.stream.client import ToolCall, ToolResult, stream_response
from tandemonecli.text import safe_markup, show_control_chars, strip_control_chars
from tandemonecli.tools.bash import (
    BASH_TOOL_NAME,
    BashResult,
    format_bash_output,
    format_declined,
    refused,
    run_bash,
)
from tandemonecli.tools.sandbox import sandbox_problem
from tandemonecli.workspace import ROOT

# No automatic highlighting: it colours whatever looks like a number or path,
# such as "/model" in a hint, so only the styles set in markup are used
console = Console(highlight=False)

# Lines of command output shown in the terminal, each cut to the terminal's
# width; the model gets all of it
OUTPUT_PREVIEW_LINES = 5

# Most completions shown at once; more than there are commands, so they all fit
MENU_HEIGHT = 10

# Seconds to wait after Esc for the rest of a key's code
ESCAPE_DELAY = 0.1

# Style of your echoed message; plain on basic terminals
USER_STYLE = "grey70 on grey15"

# Colours of the input line, drawn by prompt_toolkit. The status line is also
# drawn by Rich under a streaming reply, in the same colours (STATUS_COLOURS).
INPUT_STYLE = Style.from_dict(
    {
        "status.provider": "bold ansicyan",
        "status.model": "bold ansimagenta",
        "status.hint": "ansibrightblack",
        "status.web": "bold ansiblack bg:ansigreen",
        "status.tokens": "bold ansiblack bg:ansiyellow",
        # The line between the input and the status line, like the one above
        "rule": "dim",
        # The completion menu in the terminal's own colours, instead of
        # prompt_toolkit's grey ones, with commands and what you fill in
        # coloured as in /help, and the picked one reversed
        "completion-menu": "noinherit",
        "completion-menu.completion": "noinherit",
        "completion-menu.completion.current": "noinherit reverse",
        "completion-menu.meta.completion": "noinherit",
        "completion-menu.meta.completion.current": "noinherit reverse",
        "completion.command": "bold ansicyan",
        "completion.argument": "bold ansimagenta",
        "scrollbar.background": "noinherit",
        "scrollbar.button": "bg:ansibrightblack",
    }
)
STATUS_COLOURS = {
    "provider": "bold cyan",
    "model": "bold magenta",
    "hint": "dim",
    "web": "bold black on green",
    "tokens": "bold black on yellow",
}


class ApprovalPrompt(Prompt):
    """The "Run it?" question (a separate class, so tests can answer it)."""


def print_command(command: str, style: str) -> None:
    """Print a command exactly as it will run."""
    # Show control characters instead of hiding them, so nothing runs unseen
    console.print(
        show_control_chars(command),
        style=style,
        markup=False,
        highlight=False,
        soft_wrap=True,
    )


def confirm_command(command: str, rule: str | None = None) -> str:
    """Show the exact command the model wants to run and ask the user to approve it.

    Returns "y" (run it), "n" (don't) or, if a ``rule`` is offered, "a" (run it and
    always allow commands starting with the rule in this project). Ctrl+C or Ctrl+D
    at the prompt counts as "n".
    """
    console.print("[bold yellow]The model wants to run this command:[/bold yellow]")
    print_command(command, style="bold")
    choices = ["y", "n"]
    if rule:
        shown_rule = safe_markup(show_control_chars(rule))
        console.print(
            f"[dim]Answer [bold]a[/bold] to run it and always allow commands "
            f"starting with [bold]{shown_rule}[/bold] in this project.[/dim]"
        )
        choices.append("a")
    try:
        return ApprovalPrompt.ask(
            "Run it?",
            choices=choices,
            default="n",
            case_sensitive=False,
            console=console,
        )
    except KeyboardInterrupt, EOFError:
        console.print()
        return "n"


def approve_command(command: str) -> bool:
    """Return whether a command may run: because it's allowed, or the user approves.

    Allowed commands are shown, but run without asking. If the permissions file
    is broken, every command needs approval and no rules can be saved.
    """
    try:
        permissions = load_permissions()
    except PermissionsError as e:
        console.print(
            f"[yellow]Asking for every command, because {safe_markup(str(e))}[/yellow]"
        )
        return confirm_command(command) == "y"

    if is_allowed(command, permissions, ROOT):
        console.print("[dim]Running allowed command:[/dim]")
        print_command(command, style="dim bold")
        return True

    rule = suggest_rule(command)
    answer = confirm_command(command, rule)
    if answer == "a" and rule:
        try:
            allow_in_project(rule, ROOT)
        except PermissionsError as e:
            console.print(f"[red]Couldn't save the rule: {safe_markup(str(e))}[/red]")
        else:
            shown_rule = safe_markup(show_control_chars(rule))
            console.print(
                f"[green]Commands starting with [bold]{shown_rule}[/bold] now run "
                "without asking in this project.[/green]"
            )
    return answer in ("y", "a")


def show_bash_result(result: BashResult) -> None:
    """Print a short preview of a command's output, and whether it succeeded."""
    output = "\n".join(
        part.rstrip("\n") for part in (result.stdout, result.stderr) if part
    )
    lines = output.splitlines()
    for line in lines[:OUTPUT_PREVIEW_LINES]:
        # One screen line each, so a long line can't fill the screen
        console.print(
            strip_control_chars(line).expandtabs(),
            style="dim",
            markup=False,
            highlight=False,
            no_wrap=True,
            overflow="ellipsis",
        )
    if len(lines) > OUTPUT_PREVIEW_LINES:
        hidden = len(lines) - OUTPUT_PREVIEW_LINES
        console.print(f"[dim]… {hidden} more lines (sent to the model)[/dim]")
    if result.error:
        console.print(f"[red]✗ {safe_markup(result.error)}[/red]")
    elif result.exit_code:
        console.print(f"[red]✗ Failed with exit code {result.exit_code}[/red]")
    else:
        console.print("[green]✓ Succeeded[/green]")


def run_tool_call(call: ToolCall) -> ToolResult:
    """Run a tool call of the model, if the user approves it, and return its result.

    Every call gets a result, even an unknown or declined one, because the model
    waits for the results of all its calls before it continues. A blank line
    follows each call, to set it apart from the next one.
    """
    try:
        return _run_tool_call(call)
    finally:
        console.print()


def _run_tool_call(call: ToolCall) -> ToolResult:
    if call.name != BASH_TOOL_NAME:
        console.print(
            f"[red]The model asked for an unknown tool:[/red] {safe_markup(call.name)}"
        )
        return ToolResult(
            call.tool_call_id,
            f"Unknown tool '{call.name}'. The only tool is '{BASH_TOOL_NAME}'.",
            is_error=True,
        )

    command = call.args.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolResult(
            call.tool_call_id,
            "The bash tool needs a non-empty 'command' string.",
            is_error=True,
        )

    # Don't ask for approval of a command that can't run anyway
    if problem := sandbox_problem(ROOT):
        result = refused(problem)
        show_bash_result(result)
        return ToolResult(call.tool_call_id, format_bash_output(result), is_error=True)

    if not approve_command(command):
        console.print("[yellow]✗ Command not run.[/yellow]")
        return ToolResult(call.tool_call_id, format_declined(), is_error=True)

    bash_result = run_bash(command, ROOT)
    show_bash_result(bash_result)
    return ToolResult(
        call.tool_call_id,
        format_bash_output(bash_result),
        is_error=bash_result.error is not None,
    )


def show_user_message(user_input: str) -> None:
    """Echo your message on a full-width background, with a blank line after.

    The "You:" prefix and background bar set your message apart from the
    reply; the blank line keeps them apart on terminals without colour.
    """
    text = strip_control_chars(user_input).strip()
    message = Text("You: ", style=USER_STYLE)
    message.append(text, style=USER_STYLE)
    # Stretch the background to the full width, so your message is a clear bar
    message.append(" " * max(0, console.width - len(message.plain)), style=USER_STYLE)
    console.print(message)
    console.print()


def show_input(user_input: str) -> None:
    """Echo input that isn't a message, such as a slash command, as you typed it."""
    console.print(Text(f"> {strip_control_chars(user_input)}"))


def is_message(user_input: str, state: SlashState) -> bool:
    """Whether ``user_input`` goes to the model, rather than being a command."""
    return bool(
        user_input
        and user_input.lower() != "exit"
        and not user_input.startswith("/")
        and state.selected_provider
        and state.selected_model
    )


def run_conversation_turn(
    state: SlashState, user_input: str, provider: str, model_name: str
) -> None:
    """Send a message and run the tool calls the model makes, until it's done.

    Each command needs the user's approval, unless it's allowed. The results, also
    of declined commands, are sent back right away so the model can continue.
    """
    prompt: str | None = user_input
    tool_results: list[ToolResult] | None = None

    while True:
        result = stream_response(
            api_key=tocli_settings.SYNAPSE_API_KEY,
            prompt=prompt,
            provider=provider,
            model_name=model_name,
            thread_id=state.thread_id,
            tool_results=tool_results,
            web_search=state.web,
            max_output_tokens=state.max_output_tokens,
            footer=input_placeholder(state),
        )
        state.thread_id = result.thread_id

        # Don't act on a reply that was cut short by an error
        if result.failed or not result.tool_calls:
            return
        if result.thread_id is None:
            # Can't happen with SynapseAPI, which always sends a thread ID
            console.print("[red]Can't send the command results: no conversation.[/red]")
            return

        tool_results = [run_tool_call(call) for call in result.tool_calls]
        prompt = None


def status_parts(state: SlashState) -> list[tuple[str, str]]:
    """The pieces of the status line: the name of a colour and the text.

    Names come from the API, so their control characters are removed before
    they are shown.
    """
    parts: list[tuple[str, str]] = []
    if state.selected_provider:
        parts.append(("provider", strip_control_chars(state.selected_provider)))
        if state.selected_model:
            parts.append(("hint", " / "))
            parts.append(("model", strip_control_chars(state.selected_model)))
        else:
            parts.append(("hint", "  no model yet — /model <name>"))
    else:
        parts.append(("hint", "no provider yet — /provider <name>"))

    # Shown the whole time, so it's always clear the model can go online
    if state.web:
        parts.append(("", "  "))
        parts.append(("web", " ✓ web "))
    if state.max_output_tokens is not None:
        parts.append(("", "  "))
        parts.append(("tokens", f" max-tokens {state.max_output_tokens} "))
    return parts


def build_status_line(state: SlashState) -> StyleAndTextTuples:
    """Build the status line under the input, for prompt_toolkit."""
    return [
        (f"class:status.{name}" if name else "", text)
        for name, text in status_parts(state)
    ]


def status_renderable(state: SlashState) -> Text:
    """Build the same status line for Rich, to keep under the reply it writes."""
    line = Text(no_wrap=True, overflow="ellipsis")
    for name, text in status_parts(state):
        line.append(text, style=STATUS_COLOURS.get(name, ""))
    return line


class ScrollbarWhenNeeded(ScrollbarMargin):
    """A scrollbar only when there's more than fits, rather than always."""

    def create_margin(
        self, window_render_info: WindowRenderInfo, width: int, height: int
    ) -> StyleAndTextTuples:
        if window_render_info.content_height <= window_render_info.window_height:
            return []
        return super().create_margin(window_render_info, width, height)


def completion_menu() -> ConditionalContainer:
    """The menu of completions, shown above the input while there are any.

    Like prompt_toolkit's ``CompletionsMenu``, but part of the layout rather
    than floating over it, and with a scrollbar only when not everything fits.
    """
    return ConditionalContainer(
        Window(
            CompletionsMenuControl(),
            height=Dimension(min=1, max=MENU_HEIGHT),
            right_margins=[ScrollbarWhenNeeded()],
            dont_extend_width=True,
            # As tall as the completions, so the empty space goes above it
            dont_extend_height=True,
            style="class:completion-menu",
        ),
        filter=has_completions & ~is_done,
    )


def input_placeholder(state: SlashState) -> Group:
    """The input line as it looks while empty, to keep under a streaming reply.

    So the input stays in its place while the reply is written, instead of
    disappearing until it's done.
    """
    return Group(
        Rule(style="dim"),
        Text("> "),
        Rule(style="dim"),
        status_renderable(state),
    )


class InputLine:
    """The input line between two rules, with the status line under it.

    They sit at the bottom of the terminal, as while a reply streams in (see
    ``input_placeholder``): prompt_toolkit uses the rows from the cursor down,
    and the empty space goes above them. Slash commands are completed as you
    type, in a menu that opens upwards from the input (see ``SlashCompleter``),
    so the input stays where it is. Pick a completion with Tab or the arrow keys
    and press Enter to use it; Esc closes the menu.

    It's all erased when you press Enter, so the main loop can show your input
    once, in the style that fits it (see ``show_user_message``).
    """

    def __init__(self) -> None:
        self.state = SlashState()
        self.models = AvailableModels(providers={})
        self.buffer = Buffer(
            multiline=False,
            history=InMemoryHistory(),
            completer=SlashCompleter(lambda: self.models, lambda: self.state),
            complete_while_typing=True,
        )
        self.app: Application[str] = Application(
            layout=Layout(
                HSplit(
                    [
                        # Takes the rows not needed, to keep the rest at the bottom
                        Window(),
                        completion_menu(),
                        Window(char="─", height=1, style="class:rule"),
                        Window(
                            BufferControl(
                                self.buffer, input_processors=[BeforeInput("> ")]
                            ),
                            wrap_lines=True,
                            dont_extend_height=True,
                        ),
                        Window(char="─", height=1, style="class:rule"),
                        ConditionalContainer(
                            Window(
                                FormattedTextControl(
                                    lambda: build_status_line(self.state)
                                ),
                                height=1,
                            ),
                            filter=~is_done,
                        ),
                    ]
                )
            ),
            key_bindings=self._key_bindings(),
            style=INPUT_STYLE,
            erase_when_done=True,
        )
        # How long to wait to tell Esc from the start of a key's code, such as an
        # arrow key's; terminals send those at once, so this is plenty, and Esc
        # closes the menu without a noticeable delay (the default is 0.5 s)
        self.app.ttimeoutlen = ESCAPE_DELAY

    def ask(self, state: SlashState, models: AvailableModels) -> str:
        """Read one line, with ``state`` shown under it and ``models`` to complete.

        Raises ``KeyboardInterrupt`` on Ctrl+C and ``EOFError`` on Ctrl+D, like
        the prompts of Rich, so the main loop can end the same way.
        """
        self.state = state
        self.models = models
        self.buffer.reset()
        return self.app.run()

    def _key_bindings(self) -> KeyBindings:
        """Bind the keys that end the input; the rest are prompt_toolkit's own."""
        keys = KeyBindings()

        @keys.add("enter")
        def _accept(event: KeyPressEvent) -> None:
            # A completion you picked is used, rather than sending what's typed
            completion = self.buffer.complete_state and (
                self.buffer.complete_state.current_completion
            )
            if completion:
                self.buffer.apply_completion(completion)
                if self.buffer.text.endswith(" "):
                    # The command needs an argument: list its choices instead
                    self.buffer.start_completion()
                    return
            self.buffer.append_to_history()
            event.app.exit(result=self.buffer.text)

        # Eager: don't wait to see if Esc starts a key combination such as Alt+B
        @keys.add("escape", filter=has_completions, eager=True)
        def _close_menu(event: KeyPressEvent) -> None:
            self.buffer.cancel_completion()

        @keys.add("c-c")
        def _interrupt(event: KeyPressEvent) -> None:
            event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

        @keys.add("c-d")
        def _end_of_input(event: KeyPressEvent) -> None:
            # Ctrl+D quits on an empty line, and deletes a character otherwise
            if self.buffer.text:
                self.buffer.delete()
            else:
                event.app.exit(exception=EOFError, style="class:exiting")

        return keys


@cache
def input_line() -> InputLine:
    """The one input line, so it remembers what you typed earlier (↑ recalls it)."""
    return InputLine()


def read_user_input(state: SlashState, models: AvailableModels) -> str:
    """Ask for your next message, with the status line under the input."""
    return input_line().ask(state, models)


def main():
    user = get_user(tocli_settings.SYNAPSE_API_KEY)
    console.print(f"Hello {safe_markup(user.first_name)}! Welcome to Tandem One CLI!")
    console.print(f"Working in [bold]{safe_markup(str(ROOT))}[/bold]")
    if problem := sandbox_problem(ROOT):
        console.print(
            f"[yellow]The model can't run commands here: {safe_markup(problem)}[/yellow]"
        )
    else:
        console.print(
            "[dim]Commands run in a sandbox: only this directory can be changed.[/dim]"
        )

    # Fetching models
    with console.status("[bold green]Fetching available models..."):
        models = get_models(api_key=tocli_settings.SYNAPSE_API_KEY)

    # Initialize slash command handler
    slash_handler = SlashHandler(models=models)

    # Show only the providers; models are listed once a provider is selected
    console.print()
    console.print(slash_handler.get_providers_overview())

    console.print(
        f"\nPress [bold]Ctrl+C[/bold] to exit, or type {command_markup('exit')} "
        "to exit."
    )
    console.print(slash_handler.get_help())

    # Only now that the models are known, so an unavailable one isn't selected
    try:
        defaults = load_defaults()
    except DefaultsError as e:
        console.print(
            f"\n[yellow]Not using your defaults, because {safe_markup(str(e))}[/yellow]"
        )
    else:
        if message := slash_handler.apply_defaults(defaults):
            console.print(f"\n{message}")

    while True:
        try:
            state = slash_handler.state

            user_input = read_user_input(state, slash_handler.models).strip()

            # The input line is erased on Enter, so your input is shown only once,
            # with the line that was above it setting it apart from the output
            console.rule(style="dim")
            if is_message(user_input, state):
                show_user_message(user_input)
            else:
                # A plain line, so a rule sets it apart from the command's output
                show_input(user_input)
                console.rule(style="dim")

            if user_input.lower() == "exit":
                console.print("Goodbye!")
                break

            # Handle slash commands
            response = slash_handler.handle(user_input)
            if response:
                console.print(response.message)
                continue

            # Regular input (not a command)
            if user_input:
                if state.selected_provider and state.selected_model:
                    # Continue the current conversation, if there is one
                    run_conversation_turn(
                        state,
                        user_input,
                        provider=state.selected_provider,
                        model_name=state.selected_model,
                    )
                elif slash_handler.state.selected_provider:
                    console.print(
                        "[red]Please select a model first with /model <name>[/red]"
                    )
                else:
                    console.print("[red]Please select a provider and model first[/red]")

        except KeyboardInterrupt:
            console.print("\nGoodbye!")
            break
        except EOFError:
            break


if __name__ == "__main__":
    main()
