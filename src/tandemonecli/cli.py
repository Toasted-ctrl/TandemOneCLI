from rich.console import Console
from rich.prompt import Prompt

from tandemonecli.config.settings import tocli_settings
from tandemonecli.slash.handler import SlashHandler
from tandemonecli.startup.models import get_models
from tandemonecli.startup.user import get_user
from tandemonecli.stream.client import stream_response
from tandemonecli.text import safe_markup

console = Console()


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
                if slash_handler.state.selected_model:
                    # Continue the current conversation, if there is one
                    slash_handler.state.thread_id = stream_response(
                        api_key=tocli_settings.SYNAPSE_API_KEY,
                        prompt=user_input,
                        provider=slash_handler.state.selected_provider,
                        model_name=slash_handler.state.selected_model,
                        thread_id=slash_handler.state.thread_id,
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
