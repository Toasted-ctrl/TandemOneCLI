import pytest
from rich.console import Console

from tandemonecli.config import defaults
from tandemonecli.config.defaults import Defaults, load_defaults
from tandemonecli.slash.handler import (
    COMMANDS,
    EXIT_COMMAND,
    CommandResponse,
    SlashHandler,
    SlashState,
    bullet_list,
    command_markup,
)
from tandemonecli.startup.models import AvailableModels


def plain(markup: str) -> str:
    """The text that ``markup`` shows, without its styles."""
    console = Console(record=True, width=200, force_terminal=False)
    console.print(markup)
    return console.export_text()


@pytest.fixture
def handler(available_models) -> SlashHandler:
    return SlashHandler(models=available_models)


# --- General parsing ---------------------------------------------------------


def test_starts_with_empty_state(handler):
    assert handler.state == SlashState()


@pytest.mark.parametrize("text", ["hello", "", "provider openai", " /provider"])
def test_non_commands_return_none(handler, text):
    assert handler.handle(text) is None


def test_unknown_command_returns_error(handler):
    response = handler.handle("/nope")

    assert response is not None
    assert response.message_type == "error"
    assert "Unknown command: /nope" in response.message
    assert handler.get_help() in response.message


def test_command_name_is_case_insensitive(handler):
    response = handler.handle("/PROVIDER openai")

    assert response is not None
    assert response.message_type == "normal"
    assert handler.state.selected_provider == "openai"


@pytest.mark.parametrize("text", ["/", "/   "])
def test_bare_slash_returns_error_with_help(handler, text):
    response = handler.handle(text)

    assert response is not None
    assert response.message_type == "error"
    assert "No command given" in response.message
    assert "/provider" in response.message


def test_get_help_lists_all_commands(handler):
    help_text = handler.get_help()

    for command in ("/provider", "/model", "/new", "/clear", "/help"):
        assert command in help_text


# --- Command colours ---------------------------------------------------------


def test_command_is_cyan_and_argument_magenta():
    assert command_markup("/model <name>") == (
        "[bold cyan]/model[/bold cyan] [bold magenta]<name>[/bold magenta]"
    )


def test_command_without_argument_has_no_magenta():
    assert command_markup("/web") == "[bold cyan]/web[/bold cyan]"


def test_command_colours_render(handler):
    console = Console(record=True, width=200, force_terminal=True, highlight=False)
    console.print(handler.get_help())
    styled = console.export_text(styles=True)

    assert "\x1b[1;36m/model\x1b[0m" in styled
    assert "\x1b[1;35m<name>\x1b[0m" in styled


def test_every_usage_shown_is_coloured(handler):
    texts = [
        handler.get_help(),
        handler.get_detailed_help(),
        handler.get_providers_overview(),
        handler.handle("/provider openai").message,
    ]
    for text in texts:
        assert "[bold]/" not in text


# --- /provider ---------------------------------------------------------------


def test_provider_selects_provider_and_lists_models(handler):
    response = handler.handle("/provider openai")

    assert response is not None
    assert response.message_type == "normal"
    assert "Selected provider: [bold]openai[/bold]" in response.message
    assert "  • gpt-a\n  • gpt-b" in response.message
    assert command_markup("/model <name>") in response.message
    assert handler.state.selected_provider == "openai"


def test_provider_lists_only_its_own_models(handler):
    response = handler.handle("/provider anthropic")

    assert response is not None
    assert "  • claude-a" in response.message
    assert "gpt-a" not in response.message


def test_switching_provider_resets_model(handler):
    handler.handle("/provider openai")
    handler.handle("/model gpt-a")

    handler.handle("/provider anthropic")

    assert handler.state.selected_provider == "anthropic"
    assert handler.state.selected_model is None


def test_unknown_provider_returns_error_and_keeps_state(handler):
    handler.handle("/provider openai")

    response = handler.handle("/provider missing")

    assert response is not None
    assert response.message_type == "error"
    assert "Provider 'missing' not found" in response.message
    assert "openai, anthropic" in response.message
    assert handler.state.selected_provider == "openai"


def test_provider_without_argument_returns_error(handler):
    response = handler.handle("/provider")

    assert response is not None
    assert response.message_type == "error"
    assert handler.state.selected_provider is None


def test_provider_name_is_case_sensitive(handler):
    response = handler.handle("/provider OpenAI")

    assert response is not None
    assert response.message_type == "error"


# --- /model ------------------------------------------------------------------


def test_model_requires_provider_first(handler):
    response = handler.handle("/model gpt-a")

    assert response is not None
    assert response.message_type == "error"
    assert "select a provider first" in response.message
    assert handler.state.selected_model is None


def test_model_selects_model_from_current_provider(handler):
    handler.handle("/provider openai")

    response = handler.handle("/model gpt-b")

    assert response == CommandResponse(
        "Selected model: [bold]gpt-b[/bold] from provider [bold]openai[/bold]"
    )
    assert handler.state.selected_model == "gpt-b"


def test_model_from_other_provider_returns_error(handler):
    handler.handle("/provider openai")

    response = handler.handle("/model claude-a")

    assert response is not None
    assert response.message_type == "error"
    assert "Model 'claude-a' not found for openai" in response.message
    assert "gpt-a, gpt-b" in response.message
    assert handler.state.selected_model is None


def test_model_without_argument_returns_error(handler):
    handler.handle("/provider openai")

    response = handler.handle("/model")

    assert response is not None
    assert response.message_type == "error"
    assert handler.state.selected_model is None


# --- /clear ------------------------------------------------------------------


def test_clear_resets_selection(handler):
    handler.handle("/provider openai")
    handler.handle("/model gpt-a")

    response = handler.handle("/clear")

    assert response == CommandResponse("Selection cleared.")
    assert handler.state == SlashState()


# --- get_context_prefix ------------------------------------------------------


def test_context_prefix_empty_without_selection(handler):
    assert handler.get_context_prefix() == ""


def test_context_prefix_with_provider_only(handler):
    handler.handle("/provider anthropic")

    assert handler.get_context_prefix() == "[Using anthropic] "


def test_context_prefix_with_provider_and_model(handler):
    handler.handle("/provider anthropic")
    handler.handle("/model claude-a")

    assert handler.get_context_prefix() == "[Using anthropic/claude-a] "


# --- /new --------------------------------------------------------------------


def test_starts_without_thread(handler):
    assert handler.state.thread_id is None


def test_new_resets_thread(handler):
    handler.state.thread_id = "thread-1"

    response = handler.handle("/new")

    assert response == CommandResponse("Started a new conversation.")
    assert handler.state.thread_id is None


def test_new_keeps_selection(handler):
    handler.handle("/provider openai")
    handler.handle("/model gpt-a")

    handler.handle("/new")

    assert handler.state.selected_provider == "openai"
    assert handler.state.selected_model == "gpt-a"


def test_clear_keeps_thread(handler):
    handler.state.thread_id = "thread-1"

    handler.handle("/clear")

    assert handler.state.thread_id == "thread-1"


def test_switching_model_keeps_thread(handler):
    handler.handle("/provider openai")
    handler.handle("/model gpt-a")
    handler.state.thread_id = "thread-1"

    handler.handle("/provider anthropic")
    handler.handle("/model claude-a")

    assert handler.state.thread_id == "thread-1"


# --- Providers overview and bullet lists -------------------------------------


def test_providers_overview_lists_providers_only(handler):
    overview = handler.get_providers_overview()

    assert "Available providers:" in overview
    assert "  • openai\n  • anthropic" in overview
    assert "/provider <name>" in plain(overview)
    for model in ("gpt-a", "gpt-b", "claude-a"):
        assert model not in overview


def test_providers_overview_without_providers():
    handler = SlashHandler(models=AvailableModels(providers={}))

    overview = handler.get_providers_overview()

    assert "No providers with chat models are available" in overview


def test_bullet_list_formats_one_item_per_line():
    assert bullet_list(["a", "b"]) == "  • a\n  • b"


def test_bullet_list_escapes_markup():
    assert bullet_list(["model-[beta]"]) == "  • model-\\[beta]"


# --- /help -------------------------------------------------------------------


@pytest.mark.parametrize("text", ["/help", "/HELP", "/help me"])
def test_help_returns_detailed_help(handler, text):
    response = handler.handle(text)

    assert response == CommandResponse(handler.get_detailed_help())


def test_detailed_help_describes_every_command(handler):
    help_text = handler.get_detailed_help()

    for command in (*COMMANDS, EXIT_COMMAND):
        assert command.usage in plain(help_text)
        assert command.description in help_text
        # The description stays in the default colour
        assert f"   {command.description}" in help_text


def test_detailed_help_aligns_descriptions():
    console = Console(record=True, width=200, force_terminal=False)
    console.print(SlashHandler(models=AvailableModels({})).get_detailed_help())
    lines = console.export_text().splitlines()[1:]

    columns = {
        line.index(command.description)
        for line, command in zip(lines, (*COMMANDS, EXIT_COMMAND), strict=True)
    }
    assert len(columns) == 1


def test_short_help_lists_every_command_and_points_to_help(handler):
    help_text = handler.get_help()

    for command in COMMANDS:
        assert command.usage in plain(help_text)
    assert "Type /help for details." in plain(help_text)
    assert f"Type {command_markup('/help')} for details." in help_text


def test_every_listed_command_is_handled(handler):
    for command in COMMANDS:
        name = command.usage.split()[0]
        response = handler.handle(name)

        assert response is not None
        assert "Unknown command" not in response.message


def test_help_does_not_change_state(handler):
    handler.handle("/provider openai")
    handler.handle("/model gpt-a")
    handler.state.thread_id = "thread-1"
    before = SlashState(**vars(handler.state))

    handler.handle("/help")

    assert handler.state == before


def test_bullet_list_strips_control_chars():
    assert bullet_list(["gpt\x1b[2J"]) == "  • gpt[2J"


# --- /web --------------------------------------------------------------------


def test_web_search_is_off_by_default(handler):
    assert handler.state.web is False


def test_web_toggles_web_search(handler):
    enabled = handler.handle("/web")
    assert handler.state.web is True
    assert "enabled" in enabled.message

    disabled = handler.handle("/web")
    assert handler.state.web is False
    assert "disabled" in disabled.message


# --- /max-tokens --------------------------------------------------------------------


def test_max_output_tokens_is_unset_by_default(handler):
    assert handler.state.max_output_tokens is None


def test_max_tokens_sets_max_output_tokens(handler):
    response = handler.handle("/max-tokens 1024")

    assert handler.state.max_output_tokens == 1024
    assert response.message_type == "normal"
    assert "1024" in response.message


def test_max_tokens_without_number_resets_max_output_tokens(handler):
    handler.handle("/max-tokens 1024")

    response = handler.handle("/max-tokens")

    assert handler.state.max_output_tokens is None
    assert "reset" in response.message


@pytest.mark.parametrize("arg", ["abc", "0", "-5", "1.5", "[bold]x"])
def test_max_tokens_rejects_invalid_numbers(handler, arg):
    handler.handle("/max-tokens 1024")

    response = handler.handle(f"/max-tokens {arg}")

    assert response.message_type == "error"
    assert handler.state.max_output_tokens == 1024


# --- Defaults ----------------------------------------------------------------


def select_gpt_a(handler: SlashHandler) -> None:
    handler.handle("/provider openai")
    handler.handle("/model gpt-a")


def test_set_default_saves_current_state(handler):
    select_gpt_a(handler)
    handler.handle("/web")
    handler.handle("/max-tokens 2048")

    response = handler.handle("/set-default")

    assert response.message_type == "normal"
    assert "openai / gpt-a" in response.message
    # The max tokens aren't saved: another model may not support as many
    assert load_defaults() == Defaults(provider="openai", model="gpt-a", web=True)
    assert "max tokens" not in response.message


@pytest.mark.parametrize("commands", [[], ["/provider openai"]])
def test_set_default_needs_a_model(handler, defaults_file, commands):
    for command in commands:
        handler.handle(command)

    response = handler.handle("/set-default")

    assert response.message_type == "error"
    assert "Select a provider and model first" in response.message
    assert not defaults_file.exists()


def test_set_default_reports_when_it_cant_save(handler, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setattr(defaults, "DEFAULTS_FILE", blocker / "defaults.json")
    select_gpt_a(handler)

    response = handler.handle("/set-default")

    assert response.message_type == "error"
    assert "Couldn't save the defaults" in response.message


def test_empty_defaults_change_nothing(handler):
    assert handler.apply_defaults(Defaults()) is None
    assert handler.state == SlashState()


def test_defaults_select_an_available_model(handler):
    message = handler.apply_defaults(
        Defaults(provider="anthropic", model="claude-a", web=True)
    )

    assert handler.state == SlashState(
        selected_provider="anthropic",
        selected_model="claude-a",
        web=True,
    )
    assert message is not None
    assert "anthropic / claude-a" in message
    assert "web search on" in message


@pytest.mark.parametrize(
    ("provider", "model"),
    [("openai", "gpt-gone"), ("gone", "gpt-a"), ("anthropic", "gpt-a")],
)
def test_defaults_skip_an_unavailable_model(handler, provider, model):
    message = handler.apply_defaults(Defaults(provider=provider, model=model, web=True))

    # Neither is selected, but the other settings still apply
    assert handler.state.selected_provider is None
    assert handler.state.selected_model is None
    assert handler.state.web is True
    assert message is not None
    assert "isn't available now" in message


def test_defaults_escape_names_in_messages(handler):
    message = handler.apply_defaults(Defaults(provider="[red]x", model="[bold]y"))

    assert message is not None
    assert "\\[red]x" in message
    assert "\\[bold]y" in message
