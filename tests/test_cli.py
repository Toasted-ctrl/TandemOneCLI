from collections.abc import Iterable
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from rich.console import Console

from tandemonecli import cli
from tandemonecli.startup.models import AvailableModels
from tandemonecli.startup.user import User


@pytest.fixture
def console(monkeypatch) -> Console:
    """Replace the CLI console with one that records output."""
    recording = Console(record=True, width=200, force_terminal=False)
    monkeypatch.setattr(cli, "console", recording)
    return recording


@pytest.fixture
def fake_api(monkeypatch, available_models) -> dict[str, MagicMock]:
    """Stub out the SynapseAPI calls made by the CLI."""
    get_user = MagicMock(
        return_value=User(user_id=uuid4(), first_name="Ada", last_name="Lovelace")
    )
    get_models = MagicMock(return_value=available_models)
    stream_response = MagicMock()
    monkeypatch.setattr(cli, "get_user", get_user)
    monkeypatch.setattr(cli, "get_models", get_models)
    monkeypatch.setattr(cli, "stream_response", stream_response)
    monkeypatch.setattr(cli.tocli_settings, "SYNAPSE_API_KEY", "secret-key")
    return {
        "get_user": get_user,
        "get_models": get_models,
        "stream_response": stream_response,
    }


class FakePrompt:
    """Stands in for ``Prompt.ask``, returning or raising each input in turn."""

    def __init__(self, inputs: Iterable[str | type[BaseException]]):
        self.inputs = iter(inputs)
        self.prompts: list[str] = []

    def __call__(self, prompt_text: str, default: str = "") -> str:
        self.prompts.append(prompt_text)
        item = next(self.inputs)
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item
        return item


def run(monkeypatch, console: Console, inputs) -> tuple[str, FakePrompt]:
    fake_prompt = FakePrompt(inputs)
    monkeypatch.setattr(cli.Prompt, "ask", fake_prompt)
    cli.main()
    return console.export_text(), fake_prompt


SELECT_MODEL = ["/provider openai", "/model gpt-a"]


# --- Startup -----------------------------------------------------------------


def test_greets_user_and_lists_providers_only(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["exit"])

    assert "Hello Ada! Welcome to Tandem One CLI!" in output
    assert "Available providers:\n  • openai\n  • anthropic" in output
    for model in ("gpt-a", "gpt-b", "claude-a"):
        assert model not in output
    assert (
        "Commands: /provider <name>, /model <name>, /new, /clear, /help. "
        "Type /help for details."
    ) in output


def test_uses_api_key_from_settings(monkeypatch, console, fake_api):
    run(monkeypatch, console, ["exit"])

    fake_api["get_user"].assert_called_once_with("secret-key")
    fake_api["get_models"].assert_called_once_with(api_key="secret-key")


# --- Exiting -----------------------------------------------------------------


@pytest.mark.parametrize("command", ["exit", "EXIT", "  exit  "])
def test_exit_command_says_goodbye(monkeypatch, console, fake_api, command):
    output, _ = run(monkeypatch, console, [command])

    assert output.rstrip().endswith("Goodbye!")


def test_ctrl_c_exits_with_goodbye(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, [KeyboardInterrupt])

    assert output.rstrip().endswith("Goodbye!")


def test_ctrl_d_exits_quietly(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, [EOFError])

    assert "Goodbye!" not in output


# --- Slash commands ----------------------------------------------------------


def test_slash_command_output_is_printed(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["/provider openai", "exit"])

    assert "Selected provider: openai" in output
    assert "Available models:\n  • gpt-a\n  • gpt-b" in output


def test_bare_slash_does_not_crash(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["/", "exit"])

    assert "No command given" in output
    assert output.rstrip().endswith("Goodbye!")


def test_slash_command_is_not_streamed(monkeypatch, console, fake_api):
    run(monkeypatch, console, [*SELECT_MODEL, "/clear", "exit"])

    fake_api["stream_response"].assert_not_called()


# --- Prompt text -------------------------------------------------------------


def test_prompt_shows_current_selection(monkeypatch, console, fake_api):
    _, fake_prompt = run(monkeypatch, console, [*SELECT_MODEL, "exit"])

    no_selection, provider_only, provider_and_model = fake_prompt.prompts
    assert no_selection == "> "
    assert "openai" in provider_only
    assert "gpt-a" not in provider_only
    assert "openai" in provider_and_model
    assert "gpt-a" in provider_and_model


# --- Regular input -----------------------------------------------------------


def test_input_is_streamed_to_selected_model(monkeypatch, console, fake_api):
    run(monkeypatch, console, [*SELECT_MODEL, "  hello there  ", "exit"])

    fake_api["stream_response"].assert_called_once_with(
        api_key="secret-key",
        prompt="hello there",
        provider="openai",
        model_name="gpt-a",
        thread_id=None,
    )


def test_empty_input_is_ignored(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "", "   ", "exit"])

    fake_api["stream_response"].assert_not_called()
    assert "Please select" not in output


def test_input_without_selection_asks_for_provider_and_model(
    monkeypatch, console, fake_api
):
    output, _ = run(monkeypatch, console, ["hello", "exit"])

    assert "Please select a provider and model first" in output
    fake_api["stream_response"].assert_not_called()


def test_input_with_provider_only_asks_for_model(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["/provider openai", "hello", "exit"])

    assert "Please select a model first with /model <name>" in output
    fake_api["stream_response"].assert_not_called()


# --- Conversation threads ----------------------------------------------------


def stream_calls(fake_api) -> list:
    return fake_api["stream_response"].call_args_list


def test_thread_id_is_reused_for_next_message(monkeypatch, console, fake_api):
    fake_api["stream_response"].return_value = "thread-1"

    run(monkeypatch, console, [*SELECT_MODEL, "first", "second", "exit"])

    first, second = stream_calls(fake_api)
    assert first.kwargs["thread_id"] is None
    assert second.kwargs["thread_id"] == "thread-1"


def test_latest_thread_id_is_used(monkeypatch, console, fake_api):
    fake_api["stream_response"].side_effect = ["thread-1", "thread-2", "thread-2"]

    run(monkeypatch, console, [*SELECT_MODEL, "one", "two", "three", "exit"])

    assert [c.kwargs["thread_id"] for c in stream_calls(fake_api)] == [
        None,
        "thread-1",
        "thread-2",
    ]


def test_new_command_starts_new_conversation(monkeypatch, console, fake_api):
    fake_api["stream_response"].return_value = "thread-1"

    output, _ = run(
        monkeypatch, console, [*SELECT_MODEL, "first", "/new", "second", "exit"]
    )

    first, second = stream_calls(fake_api)
    assert first.kwargs["thread_id"] is None
    assert second.kwargs["thread_id"] is None
    assert "Started a new conversation." in output


def test_conversation_continues_after_switching_model(monkeypatch, console, fake_api):
    fake_api["stream_response"].return_value = "thread-1"

    run(
        monkeypatch,
        console,
        [
            *SELECT_MODEL,
            "first",
            "/provider anthropic",
            "/model claude-a",
            "next",
            "exit",
        ],
    )

    first, second = stream_calls(fake_api)
    assert second.kwargs["thread_id"] == "thread-1"
    assert second.kwargs["model_name"] == "claude-a"


# --- Help and errors ---------------------------------------------------------


def test_help_command_prints_command_descriptions(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["/help", "exit"])

    assert "Available commands:" in output
    assert "Start a new conversation; the model forgets earlier messages" in output
    assert "Quit Tandem One CLI (or press Ctrl+C)" in output


def test_startup_shows_short_help_only(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["exit"])

    assert "Available commands:" not in output


def test_unknown_command_prints_error_and_help(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["/nope", "exit"])

    assert "Unknown command: /nope" in output
    assert output.count("Commands:") == 2


def test_no_providers_shows_message(monkeypatch, console, fake_api):
    fake_api["get_models"].return_value = AvailableModels(providers={})

    output, _ = run(monkeypatch, console, ["exit"])

    assert "No providers with chat models are available" in output


# --- Untrusted text from the API ---------------------------------------------


def test_greeting_escapes_user_name(monkeypatch, console, fake_api):
    fake_api["get_user"].return_value = User(
        user_id=uuid4(), first_name="[red]Ada\x1b[2J", last_name="L"
    )

    output, _ = run(monkeypatch, console, ["exit"])

    assert "Hello [red]Ada[2J!" in output
    assert "\x1b" not in output


def test_prompt_escapes_provider_and_model_names(monkeypatch, console, fake_api):
    fake_api["get_models"].return_value = AvailableModels(providers={"[x]": ["m[b]"]})

    _, fake_prompt = run(monkeypatch, console, ["/provider [x]", "/model m[b]", "exit"])

    assert "\\[x]" in fake_prompt.prompts[1]
    assert "m\\[b]" in fake_prompt.prompts[2]
