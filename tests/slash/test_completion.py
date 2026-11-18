import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import fragment_list_to_text

from tandemonecli.slash.completion import SlashCompleter
from tandemonecli.slash.handler import COMMANDS, SlashState
from tandemonecli.startup.models import AvailableModels


@pytest.fixture
def state() -> SlashState:
    return SlashState()


@pytest.fixture
def completer(available_models, state) -> SlashCompleter:
    return SlashCompleter(lambda: available_models, lambda: state)


def complete(completer: SlashCompleter, text: str) -> list[str]:
    """The text each completion would leave in the input line."""
    document = Document(text)
    return [
        text[: len(text) + completion.start_position] + completion.text
        for completion in completer.get_completions(document, CompleteEvent())
    ]


def test_slash_lists_every_command_with_what_it_does(completer):
    completions = list(completer.get_completions(Document("/"), CompleteEvent()))

    assert [c.display_meta_text for c in completions] == [
        c.description for c in COMMANDS
    ]
    assert [fragment_list_to_text(c.display) for c in completions] == [
        c.usage for c in COMMANDS
    ]


def test_commands_are_filtered_by_what_you_typed(completer):
    assert complete(completer, "/m") == ["/model ", "/max-tokens "]


def test_command_names_match_whatever_their_case(completer):
    assert complete(completer, "/WE") == ["/web"]


def test_commands_without_an_argument_get_no_space(completer):
    assert complete(completer, "/new") == ["/new"]


def test_command_and_argument_are_coloured_differently(completer):
    (completion,) = completer.get_completions(Document("/prov"), CompleteEvent())

    assert completion.display == [
        ("class:completion.command", "/provider"),
        ("", " "),
        ("class:completion.argument", "<name>"),
    ]


@pytest.mark.parametrize("text", ["", "hello", "hello /web", " /web"])
def test_only_input_starting_with_a_slash_is_completed(completer, text):
    assert complete(completer, text) == []


def test_provider_lists_every_provider(completer):
    assert complete(completer, "/provider ") == [
        "/provider openai",
        "/provider anthropic",
    ]


def test_provider_is_completed_in_the_case_the_api_uses(state):
    models = AvailableModels({"OpenAI": ["gpt-5"]})
    completer = SlashCompleter(lambda: models, lambda: state)

    assert complete(completer, "/provider open") == ["/provider OpenAI"]
    assert complete(completer, "/PROVIDER o") == ["/PROVIDER OpenAI"]


def test_model_lists_the_models_of_the_selected_provider(completer, state):
    state.selected_provider = "openai"

    assert complete(completer, "/model ") == ["/model gpt-a", "/model gpt-b"]
    assert complete(completer, "/model gpt-b") == ["/model gpt-b"]


def test_model_lists_nothing_without_a_provider(completer):
    assert complete(completer, "/model ") == []


def test_completer_uses_the_current_models_and_selection(state):
    models = AvailableModels({})
    completer = SlashCompleter(lambda: models, lambda: state)
    assert complete(completer, "/provider ") == []

    models = AvailableModels({"later": ["m"]})
    state.selected_provider = "later"

    assert complete(completer, "/provider ") == ["/provider later"]
    assert complete(completer, "/model ") == ["/model m"]


@pytest.mark.parametrize(
    "text", ["/web on", "/provider openai x", "/max-tokens 5", "/nope x"]
)
def test_no_completion_after_the_argument_or_for_other_commands(completer, text):
    assert complete(completer, text) == []


def test_names_from_the_api_are_shown_without_control_characters(state):
    models = AvailableModels({"evil\x1b[2J": []})
    completer = SlashCompleter(lambda: models, lambda: state)

    (completion,) = completer.get_completions(Document("/provider "), CompleteEvent())

    assert "\x1b" not in fragment_list_to_text(completion.display)
