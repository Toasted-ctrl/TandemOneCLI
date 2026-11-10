import pytest
from rich.console import Console

from tandemonecli.slash.handler import (
    COMMANDS,
    EXIT_COMMAND,
    CommandResponse,
    SlashHandler,
    SlashState,
    bullet_list,
)
from tandemonecli.startup.models import AvailableModels


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


# --- /provider ---------------------------------------------------------------


def test_provider_selects_provider_and_lists_models(handler):
    response = handler.handle("/provider openai")

    assert response is not None
    assert response.message_type == "normal"
    assert "Selected provider: [bold]openai[/bold]" in response.message
    assert "  • gpt-a\n  • gpt-b" in response.message
    assert "/model <name>" in response.message
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
    assert "/provider <name>" in overview
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
        assert command.usage in help_text
        assert command.description in help_text


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
        assert command.usage in help_text
    assert "Type [bold]/help[/bold] for details." in help_text


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
