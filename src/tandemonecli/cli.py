from rich.console import Console
from rich.prompt import Confirm, Prompt

from tandemonecli.config.settings import tocli_settings
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
    run_bash,
)

console = Console()

# Lines of command output shown in the terminal; the model gets all of it
OUTPUT_PREVIEW_LINES = 20


def confirm_command(command: str) -> bool:
    """Show the exact command the model wants to run and ask the user to approve it.

    Ctrl+C or Ctrl+D at the prompt counts as "no".
    """
    console.print("[bold yellow]The model wants to run this command:[/bold yellow]")
    # Show control characters instead of hiding them, so nothing runs unseen
    console.print(
        show_control_chars(command),
        style="bold",
        markup=False,
        highlight=False,
        soft_wrap=True,
    )
    try:
        return Confirm.ask("Run it?", default=False, console=console)
    except KeyboardInterrupt, EOFError:
        console.print()
        return False


def show_bash_result(result: BashResult) -> None:
    """Print a preview of a command's output for the user."""
    output = "\n".join(
        part.rstrip("\n") for part in (result.stdout, result.stderr) if part
    )
    lines = output.splitlines()
    preview = "\n".join(lines[:OUTPUT_PREVIEW_LINES])
    if preview:
        console.print(
            strip_control_chars(preview),
            style="dim",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
    if len(lines) > OUTPUT_PREVIEW_LINES:
        hidden = len(lines) - OUTPUT_PREVIEW_LINES
        console.print(f"[dim]... {hidden} more lines (sent to the model)[/dim]")
    if result.error:
        console.print(f"[red]{safe_markup(result.error)}[/red]")
    elif result.exit_code:
        console.print(f"[red]Exit code {result.exit_code}[/red]")


def run_tool_call(call: ToolCall) -> ToolResult:
    """Run a tool call of the model, if the user approves it, and return its result.

    Every call gets a result, even an unknown or declined one, because the model
    waits for the results of all its calls before it continues.
    """
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

    if not confirm_command(command):
        console.print("[yellow]Command not run.[/yellow]")
        return ToolResult(call.tool_call_id, format_declined(), is_error=True)

    bash_result = run_bash(command)
    show_bash_result(bash_result)
    return ToolResult(
        call.tool_call_id,
        format_bash_output(bash_result),
        is_error=bash_result.error is not None,
    )


def run_conversation_turn(
    state: SlashState, user_input: str, provider: str, model_name: str
) -> None:
    """Send a message and run the tool calls the model makes, until it's done.

    Each command needs the user's approval. The results, also of declined commands,
    are sent back right away so the model can continue.
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


def main():
    user = get_user(tocli_settings.SYNAPSE_API_KEY)
    console.print(f"Hello {safe_markup(user.first_name)}! Welcome to Tandem One CLI!")

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
            # Build prompt with context and Rich markup
            # Names come from the API, so escape them before adding markup
            state = slash_handler.state
            provider = safe_markup(state.selected_provider or "")
            model = safe_markup(state.selected_model or "")
            if state.selected_model:
                prompt_text = (
                    f"> [ [bold cyan]{provider}[/bold cyan] / "
                    f"[bold magenta]{model}[/bold magenta] ] "
                )
            elif state.selected_provider:
                prompt_text = f"> [ [bold cyan]{provider}[/bold cyan] ] "
            else:
                prompt_text = "> "

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
