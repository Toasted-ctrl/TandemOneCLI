from rich.console import Console
from rich.prompt import Prompt
from rich.text import Text

from tandemonecli.config.settings import tocli_settings
from tandemonecli.permissions import (
    PermissionsError,
    allow_in_project,
    is_allowed,
    load_permissions,
    suggest_rule,
)
from tandemonecli.slash.handler import SlashHandler, SlashState
from tandemonecli.startup.models import get_models
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

console = Console()

# Lines of command output shown in the terminal, each cut to the terminal's
# width; the model gets all of it
OUTPUT_PREVIEW_LINES = 5

# Style of your echoed message; plain on basic terminals
USER_STYLE = "grey70 on grey15"


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


def build_prompt_text(state: SlashState) -> str:
    """Build the input prompt: the selected provider/model and setting badges."""
    # Names come from the API, so escape them before adding markup
    provider = safe_markup(state.selected_provider or "")
    model = safe_markup(state.selected_model or "")
    if state.selected_model:
        context = (
            f"[ [bold cyan]{provider}[/bold cyan] / "
            f"[bold magenta]{model}[/bold magenta] ] "
        )
    elif state.selected_provider:
        context = f"[ [bold cyan]{provider}[/bold cyan] ] "
    else:
        context = ""

    # Shown on every prompt so it's always clear the model can go online
    web_badge = "[bold black on green] ✓ web [/bold black on green] "
    max_tokens_badge = (
        f"[bold black on yellow] max-tokens {state.max_output_tokens} [/bold black on yellow] "
        if state.max_output_tokens is not None
        else ""
    )
    return f"> {context}{web_badge if state.web else ''}{max_tokens_badge}"


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
        "\nPress [bold]Ctrl+C[/bold] to exit, or type [bold]exit[/bold] to exit."
    )
    console.print(slash_handler.get_help())

    while True:
        try:
            state = slash_handler.state
            prompt_text = build_prompt_text(state)

            # A line above the prompt sets your input apart from the output
            console.rule(style="dim")
            user_input = Prompt.ask(prompt_text, default="").strip()

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
                    show_user_message(user_input)
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
