import threading
import time
from collections.abc import Iterable
from unittest.mock import ANY, MagicMock
from uuid import uuid4

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.formatted_text.utils import fragment_list_to_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from tandemonecli import cli
from tandemonecli.config.defaults import Defaults, save_defaults
from tandemonecli.permissions import Permissions, load_permissions, save_permissions
from tandemonecli.slash.handler import SlashState
from tandemonecli.startup.models import AvailableModels
from tandemonecli.startup.user import User
from tandemonecli.stream import client
from tandemonecli.stream.client import StreamResult, ToolCall, ToolResult


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
    stream_response = MagicMock(return_value=StreamResult(None))
    # The sandbox works, unless a test says otherwise
    sandbox_problem = MagicMock(return_value=None)
    monkeypatch.setattr(cli, "sandbox_problem", sandbox_problem)
    monkeypatch.setattr(cli, "get_user", get_user)
    monkeypatch.setattr(cli, "get_models", get_models)
    monkeypatch.setattr(cli, "stream_response", stream_response)
    monkeypatch.setattr(cli.tocli_settings, "SYNAPSE_API_KEY", "secret-key")
    return {
        "get_user": get_user,
        "get_models": get_models,
        "stream_response": stream_response,
        "sandbox_problem": sandbox_problem,
    }


class FakePrompt:
    """Stands in for ``read_user_input``, returning or raising each input in turn.

    Records the status line shown under the input at each turn, as plain text.
    """

    def __init__(self, inputs: Iterable[str | type[BaseException]]):
        self.inputs = iter(inputs)
        self.prompts: list[str] = []

    def __call__(self, state, models) -> str:
        self.prompts.append(fragment_list_to_text(cli.build_status_line(state)))
        item = next(self.inputs)
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item
        return item


def run(monkeypatch, console: Console, inputs) -> tuple[str, FakePrompt]:
    fake_prompt = FakePrompt(inputs)
    monkeypatch.setattr(cli, "read_user_input", fake_prompt)
    cli.main()
    return console.export_text(), fake_prompt


SELECT_MODEL = ["/provider openai", "/model gpt-a"]


# --- Startup -----------------------------------------------------------------


def test_warns_at_startup_when_sandbox_is_unavailable(monkeypatch, console, fake_api):
    fake_api["sandbox_problem"].return_value = "bwrap isn't installed"

    output, _ = run(monkeypatch, console, ["exit"])

    assert "The model can't run commands here: bwrap isn't installed" in output
    assert "Commands run in a sandbox" not in output


def test_line_is_drawn_above_every_prompt(monkeypatch, console, fake_api):
    output, prompt = run(monkeypatch, console, ["/help", "exit"])

    rules = [line for line in output.splitlines() if set(line) == {"\u2500"}]
    # One above and one below each input, which is shown again after Enter
    assert len(rules) == 2 * len(prompt.prompts) == 4
    assert output.rstrip().splitlines()[-4:] == [
        rules[-1],
        "> exit",
        rules[-1],
        "Goodbye!",
    ]


def test_greets_user_and_lists_providers_only(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["exit"])

    assert "Hello Ada! Welcome to Tandem One CLI!" in output
    assert f"Working in {cli.ROOT}" in output
    assert "Commands run in a sandbox: only this directory can be changed." in output
    assert "Available providers:\n  • openai\n  • anthropic" in output
    for model in ("gpt-a", "gpt-b", "claude-a"):
        assert model not in output
    assert (
        "Commands: /provider <name>, /model <name>, /new, /clear, /help, /web, "
        "/max-tokens <tokens>, /set-default. "
        "Type /help for details."
    ) in output


def test_uses_api_key_from_settings(monkeypatch, console, fake_api):
    run(monkeypatch, console, ["exit"])

    fake_api["get_user"].assert_called_once_with("secret-key")
    fake_api["get_models"].assert_called_once_with(api_key="secret-key")


# --- Defaults ----------------------------------------------------------------


def test_starts_with_saved_defaults(monkeypatch, console, fake_api):
    save_defaults(Defaults(provider="openai", model="gpt-b", web=True))

    output, prompt = run(monkeypatch, console, ["exit"])

    assert "Using your defaults: openai / gpt-b" in output
    status = prompt.prompts[0]
    assert status.startswith("openai / gpt-b")
    assert "✓ web" in status
    assert "max-tokens" not in status


def test_defaults_are_applied_only_after_models_are_loaded(
    monkeypatch, console, fake_api
):
    order = []
    fake_api["get_models"].side_effect = lambda **_: (
        order.append("models") or fake_api["get_models"].return_value
    )
    apply_defaults = cli.SlashHandler.apply_defaults
    monkeypatch.setattr(
        cli.SlashHandler,
        "apply_defaults",
        lambda self, defaults: (
            order.append("defaults") or apply_defaults(self, defaults)
        ),
    )

    run(monkeypatch, console, ["exit"])

    assert order == ["models", "defaults"]


def test_unavailable_default_model_is_not_selected(monkeypatch, console, fake_api):
    save_defaults(Defaults(provider="openai", model="gpt-gone", web=True))

    output, prompt = run(monkeypatch, console, ["exit"])

    assert "isn't available now" in output
    assert prompt.prompts[0].startswith("no provider yet")
    assert "✓ web" in prompt.prompts[0]


def test_broken_defaults_file_is_reported_not_fatal(
    monkeypatch, console, fake_api, defaults_file
):
    defaults_file.parent.mkdir(parents=True, exist_ok=True)
    defaults_file.write_text("not json", encoding="utf-8")

    output, prompt = run(monkeypatch, console, ["exit"])

    assert "Not using your defaults, because" in output
    assert prompt.prompts[0].startswith("no provider yet")
    assert output.rstrip().endswith("Goodbye!")


def test_set_default_then_restart_uses_it(monkeypatch, console, fake_api):
    run(monkeypatch, console, [*SELECT_MODEL, "/web", "/set-default", "exit"])

    _, prompt = run(monkeypatch, console, ["exit"])

    assert prompt.prompts[0].startswith("openai / gpt-a")
    assert "✓ web" in prompt.prompts[0]


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


# --- Status line -------------------------------------------------------------


def test_status_line_shows_current_selection(monkeypatch, console, fake_api):
    _, fake_prompt = run(monkeypatch, console, [*SELECT_MODEL, "exit"])

    no_selection, provider_only, provider_and_model = fake_prompt.prompts
    assert "no provider yet" in no_selection
    assert "openai" in provider_only
    assert "no model yet" in provider_only
    assert "gpt-a" not in provider_only
    assert "openai" in provider_and_model
    assert "gpt-a" in provider_and_model


def test_status_line_shows_web_badge_only_while_web_search_is_on(
    monkeypatch, console, fake_api
):
    _, fake_prompt = run(monkeypatch, console, ["/web", "/web", "exit"])

    before, enabled, disabled = fake_prompt.prompts
    assert "web" not in before
    assert "✓ web" in enabled
    assert "web" not in disabled


def test_status_line_shows_web_badge_after_selection(monkeypatch, console, fake_api):
    _, fake_prompt = run(monkeypatch, console, [*SELECT_MODEL, "/web", "exit"])

    prompt = fake_prompt.prompts[-1]
    assert prompt.index("gpt-a") < prompt.index("✓ web")


def test_web_search_setting_is_sent_with_input(monkeypatch, console, fake_api):
    run(monkeypatch, console, [*SELECT_MODEL, "/web", "hello", "exit"])

    assert fake_api["stream_response"].call_args.kwargs["web_search"] is True


def test_status_line_shows_max_tokens_only_while_set(monkeypatch, console, fake_api):
    _, fake_prompt = run(
        monkeypatch, console, ["/max-tokens 512", "/max-tokens", "exit"]
    )

    before, limited, reset = fake_prompt.prompts
    assert "max-tokens" not in before
    assert "max-tokens 512" in limited
    assert "max-tokens" not in reset


def test_max_output_tokens_is_sent_with_input(monkeypatch, console, fake_api):
    run(monkeypatch, console, [*SELECT_MODEL, "/max-tokens 512", "hello", "exit"])

    assert fake_api["stream_response"].call_args.kwargs["max_output_tokens"] == 512


# --- Regular input -----------------------------------------------------------


def test_input_is_streamed_to_selected_model(monkeypatch, console, fake_api):
    run(monkeypatch, console, [*SELECT_MODEL, "  hello there  ", "exit"])

    fake_api["stream_response"].assert_called_once_with(
        api_key="secret-key",
        prompt="hello there",
        provider="openai",
        model_name="gpt-a",
        thread_id=None,
        tool_results=None,
        web_search=False,
        max_output_tokens=None,
        footer=ANY,
    )


def test_empty_input_line_is_shown_while_the_reply_streams(
    monkeypatch, console, fake_api
):
    run(monkeypatch, console, [*SELECT_MODEL, "/web", "hello", "exit"])

    footer = fake_api["stream_response"].call_args.kwargs["footer"]
    lines = [
        "".join(segment.text for segment in line).rstrip()
        for line in console.render_lines(footer, pad=False)
    ]
    # Just like the input line: a rule, an empty prompt, a rule, the status line
    assert len(lines) == 4
    assert set(lines[0]) == set(lines[2]) == {"\u2500"}
    assert lines[1] == ">"
    assert lines[3].startswith("openai / gpt-a")
    assert "✓ web" in lines[3]


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
    fake_api["stream_response"].return_value = StreamResult("thread-1")

    run(monkeypatch, console, [*SELECT_MODEL, "first", "second", "exit"])

    first, second = stream_calls(fake_api)
    assert first.kwargs["thread_id"] is None
    assert second.kwargs["thread_id"] == "thread-1"


def test_latest_thread_id_is_used(monkeypatch, console, fake_api):
    fake_api["stream_response"].side_effect = [
        StreamResult("thread-1"),
        StreamResult("thread-2"),
        StreamResult("thread-2"),
    ]

    run(monkeypatch, console, [*SELECT_MODEL, "one", "two", "three", "exit"])

    assert [c.kwargs["thread_id"] for c in stream_calls(fake_api)] == [
        None,
        "thread-1",
        "thread-2",
    ]


def test_new_command_starts_new_conversation(monkeypatch, console, fake_api):
    fake_api["stream_response"].return_value = StreamResult("thread-1")

    output, _ = run(
        monkeypatch, console, [*SELECT_MODEL, "first", "/new", "second", "exit"]
    )

    first, second = stream_calls(fake_api)
    assert first.kwargs["thread_id"] is None
    assert second.kwargs["thread_id"] is None
    assert "Started a new conversation." in output


def test_conversation_continues_after_switching_model(monkeypatch, console, fake_api):
    fake_api["stream_response"].return_value = StreamResult("thread-1")

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


def test_status_line_strips_control_characters_from_names(
    monkeypatch, console, fake_api
):
    fake_api["get_models"].return_value = AvailableModels(
        providers={"[x]\x1b[2J": ["m[b]\x07"]}
    )

    _, fake_prompt = run(
        monkeypatch, console, ["/provider [x]\x1b[2J", "/model m[b]\x07", "exit"]
    )

    # Shown as text, so brackets stay; escape sequences can't reach the terminal
    assert "[x]" in fake_prompt.prompts[1]
    assert "m[b]" in fake_prompt.prompts[2]
    assert "\x1b" not in fake_prompt.prompts[2]
    assert "\x07" not in fake_prompt.prompts[2]


# --- Input line --------------------------------------------------------------


def type_into_input_line(
    *steps: str,
    state: SlashState | None = None,
    models: AvailableModels | None = None,
):
    """Run one input line on a fake terminal, as if each step were typed in turn.

    Completions are found in the background, so steps are typed with a pause in
    between, like a person would, for the menu to be filled before the next key.
    """
    with create_pipe_input() as pipe:

        def type_steps() -> None:
            for step in steps:
                time.sleep(0.05 if step else cli.ESCAPE_DELAY + 0.2)
                pipe.send_text(step)

        typist = threading.Thread(target=type_steps)
        typist.start()
        try:
            with create_app_session(input=pipe, output=DummyOutput()):
                line = cli.InputLine()
                answer = line.ask(state or SlashState(), models or AvailableModels({}))
        finally:
            typist.join()
        return line, answer


TAB = "\t"
ENTER = "\r"
# Nothing typed for longer than the input line waits to see if Esc starts a key's
# code, so a lone Esc counts as Esc
PAUSE = ""


def test_tab_and_enter_complete_and_run_a_command():
    _, answer = type_into_input_line("/we", TAB, ENTER)

    assert answer == "/web"


def test_arrow_keys_pick_from_the_menu():
    down = "\x1b[B"

    # The arrow key's code starts with Esc, but is still read as one key
    _, answer = type_into_input_line("/n", down, ENTER)

    assert answer == "/new"


def test_command_that_needs_an_argument_lists_its_choices(available_models):
    # Enter on /provider doesn't send it, but lists the providers to pick from
    _, answer = type_into_input_line(
        "/pro", TAB, ENTER, TAB, ENTER, models=available_models
    )

    assert answer == "/provider openai"


def test_model_is_completed_from_the_selected_provider(available_models):
    state = SlashState(selected_provider="anthropic")

    _, answer = type_into_input_line(
        "/model ", TAB, ENTER, state=state, models=available_models
    )

    assert answer == "/model claude-a"


def test_escape_closes_the_menu_and_keeps_what_you_typed(available_models):
    _, answer = type_into_input_line(
        "/pro", TAB, "\x1b", PAUSE, ENTER, models=available_models
    )

    # Tab had filled in /provider; Esc puts back what you typed
    assert answer == "/pro"


def test_escape_closes_the_menu_right_away(available_models):
    # The x comes well within the second prompt_toolkit would otherwise wait
    # for a key combination starting with Esc, and is still typed as an x
    _, answer = type_into_input_line(
        "/provider ", "\x1b", PAUSE, "x", ENTER, models=available_models
    )

    assert answer == "/provider x"


def test_enter_sends_what_you_typed_if_no_completion_is_picked():
    models = AvailableModels({"openai": ["gpt-a-mini", "gpt-a"]})
    state = SlashState(selected_provider="openai")

    # gpt-a-mini also matches, and comes first, but wasn't picked
    _, answer = type_into_input_line("/model gpt-a", ENTER, state=state, models=models)

    assert answer == "/model gpt-a"


def test_input_line_completes_with_the_models_passed_to_ask(available_models):
    line, _ = type_into_input_line(ENTER, models=available_models)

    assert line.models is available_models


def test_input_line_returns_what_you_typed():
    line, answer = type_into_input_line("hello there\r")

    assert answer == "hello there"
    # Kept for the next line, which can recall it with the up arrow
    assert line.buffer.history.get_strings() == ["hello there"]


def test_input_line_is_erased_when_you_press_enter():
    line, _ = type_into_input_line("hello there\r")

    # The main loop shows your input instead, so it isn't on screen twice
    assert line.app.erase_when_done


def test_message_is_shown_once_as_you(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "hello there", "exit"])

    assert output.count("hello there") == 1
    assert "You: hello there" in output
    assert "> hello there" not in output
    # A line above, but only a blank line before the reply: the bar is enough
    lines = output.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("You: hello"))
    assert set(lines[at - 1]) == {"\u2500"}
    assert lines[at + 1] == ""


def test_command_is_shown_as_typed(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["/provider openai", "hello", "exit"])

    assert "> /provider openai" in output
    # Without a model, input isn't sent, so it isn't shown as your message
    assert "> hello" in output
    assert "You:" not in output
    assert "> exit" in output


def test_input_line_layout_keeps_the_input_at_the_bottom():
    line = cli.InputLine()
    windows = line.app.layout.container.get_children()

    # Empty space, the completion menu, the input between two lines, then the
    # status line, so the input stays at the bottom
    assert isinstance(windows[1].content.content, cli.CompletionsMenuControl)
    assert windows[2].char == windows[4].char == "\u2500"
    assert isinstance(windows[3].content, cli.BufferControl)


def test_input_line_raises_keyboard_interrupt_on_ctrl_c():
    with pytest.raises(KeyboardInterrupt):
        type_into_input_line("half a message\x03")


def test_input_line_raises_eof_error_on_ctrl_d_when_empty():
    with pytest.raises(EOFError):
        type_into_input_line("\x04")


def test_ctrl_d_deletes_a_character_while_you_are_typing():
    _, answer = type_into_input_line("abc\x01\x04\r")

    # Ctrl+A moves to the start, so Ctrl+D deletes the "a" instead of quitting
    assert answer == "bc"


# --- Bash commands -----------------------------------------------------------


@pytest.fixture
def confirm(monkeypatch) -> MagicMock:
    """Stub the "Run it?" prompt; answers "n" unless a test changes it."""
    fake = MagicMock(return_value="n")
    monkeypatch.setattr(cli.ApprovalPrompt, "ask", fake)
    return fake


@pytest.fixture(autouse=True)
def ask_for_every_command(permissions_file):
    """Turn off the built-in read-only commands, so commands like ``ls`` need
    approval. Tests of allowed commands write their own permissions."""
    save_permissions(Permissions(allow_read_only_commands=False), permissions_file)


@pytest.fixture
def fake_bash(monkeypatch) -> MagicMock:
    fake = MagicMock(
        return_value=cli.BashResult(stdout="index.js\n", stderr="", exit_code=0)
    )
    monkeypatch.setattr(cli, "run_bash", fake)
    return fake


def bash_call(command: str, call_id: str = "call-1") -> ToolCall:
    return ToolCall(call_id, "bash", {"command": command})


def asks_for(*calls: ToolCall) -> StreamResult:
    return StreamResult("thread-1", "I'll have a look.", tool_calls=calls)


ASKS_FOR_LS = asks_for(bash_call("ls -la"))
LS_OUTPUT = (
    "<bash_output>\n<stdout>\nindex.js\n</stdout>\n<exit_code>0</exit_code>\n"
    "</bash_output>"
)


def prompts_sent(fake_api) -> list[str | None]:
    return [c.kwargs["prompt"] for c in stream_calls(fake_api)]


def results_sent(fake_api) -> list[list[ToolResult] | None]:
    return [c.kwargs["tool_results"] for c in stream_calls(fake_api)]


def test_approved_command_runs_and_result_is_sent_back(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.return_value = "y"
    fake_api["stream_response"].side_effect = [
        ASKS_FOR_LS,
        StreamResult("thread-1", "There is an index.js file."),
    ]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "what's here?", "exit"])

    fake_bash.assert_called_once_with("ls -la", cli.ROOT)
    assert prompts_sent(fake_api) == ["what's here?", None]
    assert results_sent(fake_api) == [None, [ToolResult("call-1", LS_OUTPUT)]]
    assert [c.kwargs["thread_id"] for c in stream_calls(fake_api)] == [
        None,
        "thread-1",
    ]
    assert "The model wants to run this command:\nls -la" in output
    assert "index.js" in output


def test_commands_keep_running_until_model_stops_asking(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.return_value = "y"
    fake_api["stream_response"].side_effect = [
        ASKS_FOR_LS,
        asks_for(bash_call("pwd", "call-2")),
        StreamResult("thread-1", "Done."),
    ]

    run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert [c.args[0] for c in fake_bash.call_args_list] == ["ls -la", "pwd"]
    assert len(stream_calls(fake_api)) == 3
    assert results_sent(fake_api)[2][0].tool_call_id == "call-2"


def test_several_calls_in_one_reply_are_answered_together(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.side_effect = ["y", "n"]
    fake_api["stream_response"].side_effect = [
        asks_for(bash_call("ls", "a"), bash_call("rm x", "b")),
        StreamResult("thread-1", "OK."),
    ]

    run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    fake_bash.assert_called_once_with("ls", cli.ROOT)
    sent = results_sent(fake_api)[1]
    assert [(r.tool_call_id, r.is_error) for r in sent] == [("a", False), ("b", True)]


def test_declined_command_is_reported_right_away(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    fake_api["stream_response"].side_effect = [
        ASKS_FOR_LS,
        StreamResult("thread-1", "OK, what should I do instead?"),
        StreamResult("thread-1", "OK."),
    ]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "use git", "exit"])

    fake_bash.assert_not_called()
    assert "Command not run." in output
    assert results_sent(fake_api)[1] == [
        ToolResult("call-1", cli.format_declined(), is_error=True)
    ]
    # The next message is sent as it is
    assert prompts_sent(fake_api)[2] == "use git"


def test_command_is_refused_without_asking_when_sandbox_is_unavailable(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    fake_api["sandbox_problem"].return_value = "bwrap isn't installed"
    fake_api["stream_response"].side_effect = [
        ASKS_FOR_LS,
        StreamResult("thread-1", "I can't run commands here."),
    ]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    confirm.assert_not_called()
    fake_bash.assert_not_called()
    assert "Command not run: bwrap isn't installed" in output
    assert results_sent(fake_api)[1] == [
        ToolResult(
            "call-1",
            cli.format_bash_output(cli.refused("bwrap isn't installed")),
            is_error=True,
        )
    ]


@pytest.mark.parametrize("error", [KeyboardInterrupt, EOFError])
def test_ctrl_c_at_confirm_declines_without_exiting(
    monkeypatch, console, fake_api, confirm, fake_bash, error
):
    confirm.side_effect = error
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    output, prompt = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    fake_bash.assert_not_called()
    assert "Command not run." in output
    assert results_sent(fake_api)[1][0].is_error is True
    assert "Goodbye!" in output


def test_tool_calls_in_failed_reply_are_ignored(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    fake_api["stream_response"].return_value = StreamResult(
        "thread-1", tool_calls=(bash_call("rm -rf x"),), failed=True
    )

    run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    confirm.assert_not_called()
    fake_bash.assert_not_called()
    assert len(stream_calls(fake_api)) == 1


def test_reply_without_tool_calls_asks_nothing(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    fake_api["stream_response"].return_value = StreamResult(
        "thread-1", "Write <bash>\nls\n</bash> to run ls."
    )

    run(monkeypatch, console, [*SELECT_MODEL, "hi", "exit"])

    # Commands are only taken from tool calls, never from the reply's text
    confirm.assert_not_called()
    assert len(stream_calls(fake_api)) == 1


def test_unknown_tool_gets_error_result(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    fake_api["stream_response"].side_effect = [
        asks_for(ToolCall("call-1", "[b]python[/b]", {"code": "1"})),
        StreamResult("thread-1"),
    ]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    confirm.assert_not_called()
    assert "unknown tool: [b]python[/b]" in output
    result = results_sent(fake_api)[1][0]
    assert result.is_error is True
    assert "Unknown tool" in result.content


@pytest.mark.parametrize("args", [{}, {"command": ""}, {"command": 5}])
def test_bash_call_without_command_gets_error_result(
    monkeypatch, console, fake_api, confirm, fake_bash, args
):
    fake_api["stream_response"].side_effect = [
        asks_for(ToolCall("call-1", "bash", args)),
        StreamResult("thread-1"),
    ]

    run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    confirm.assert_not_called()
    assert results_sent(fake_api)[1][0].is_error is True


def test_confirm_shows_hidden_control_chars(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    fake_api["stream_response"].side_effect = [
        asks_for(bash_call("echo hi\x1b[1A\x1b[2Krm -rf x")),
        StreamResult("thread-1"),
    ]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert "echo hi\\x1b[1A\\x1b[2Krm -rf x" in output


def test_long_command_output_is_cut_short_on_screen(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.return_value = "y"
    lines = "".join(f"line {i}\n" for i in range(30))
    fake_bash.return_value = cli.BashResult(stdout=lines, stderr="", exit_code=0)
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert "line 4\n" in output
    assert "line 5\n" not in output
    assert "… 25 more lines (sent to the model)" in output
    # The model still gets all of it
    assert "line 29" in results_sent(fake_api)[1][0].content


def test_failed_command_shows_exit_code(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.return_value = "y"
    fake_bash.return_value = cli.BashResult(stdout="", stderr="nope\n", exit_code=2)
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert "nope" in output
    assert "✗ Failed with exit code 2" in output
    assert "Succeeded" not in output
    # The command ran, so its result isn't an error, even with a non-zero exit code
    assert results_sent(fake_api)[1][0].is_error is False


def test_successful_command_is_marked(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.return_value = "y"
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    # The output preview, the mark, and a blank line before what comes next
    assert "index.js\n✓ Succeeded\n\n" in output


def test_long_output_lines_are_cut_to_one_screen_line(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.return_value = "y"
    long_line = "x" * 500
    fake_bash.return_value = cli.BashResult(
        stdout=f"{long_line}\n", stderr="", exit_code=0
    )
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    shown = next(line for line in output.splitlines() if line.startswith("xxx"))
    assert len(shown) == console.width
    assert shown.endswith("…")
    assert long_line in results_sent(fake_api)[1][0].content


def test_declined_command_is_marked_and_followed_by_blank_line(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert "✗ Command not run.\n\n" in output


def test_timed_out_command_is_marked(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.return_value = "y"
    fake_bash.return_value = cli.BashResult(
        stdout="", stderr="", exit_code=None, error="Command timed out"
    )
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert "✗ Command timed out" in output
    assert "Succeeded" not in output


def test_timed_out_command_is_an_error_result(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.return_value = "y"
    fake_bash.return_value = cli.BashResult(
        stdout="", stderr="", exit_code=None, error="Command timed out"
    )
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert results_sent(fake_api)[1][0].is_error is True


# --- Allowed commands --------------------------------------------------------


def test_read_only_command_runs_without_asking(
    monkeypatch, console, fake_api, confirm, fake_bash, permissions_file
):
    save_permissions(Permissions(), permissions_file)
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    confirm.assert_not_called()
    fake_bash.assert_called_once_with("ls -la", cli.ROOT)
    assert "Running allowed command:\nls -la" in output
    assert results_sent(fake_api)[1] == [ToolResult("call-1", LS_OUTPUT)]


def test_project_rule_runs_command_without_asking(
    monkeypatch, console, fake_api, confirm, fake_bash, permissions_file
):
    cli.allow_in_project("uv run pytest", cli.ROOT)
    fake_api["stream_response"].side_effect = [
        asks_for(bash_call("uv run pytest -q")),
        StreamResult("thread-1"),
    ]

    run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    confirm.assert_not_called()
    fake_bash.assert_called_once_with("uv run pytest -q", cli.ROOT)


def test_always_runs_command_and_saves_rule_for_project(
    monkeypatch, console, fake_api, confirm, fake_bash, permissions_file
):
    confirm.return_value = "a"
    fake_api["stream_response"].side_effect = [
        asks_for(bash_call("uv run pytest -q", "a")),
        asks_for(bash_call("uv run pytest tests/x.py", "b")),
        StreamResult("thread-1"),
    ]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    # Asked only the first time
    confirm.assert_called_once()
    assert confirm.call_args.kwargs["choices"] == ["y", "n", "a"]
    assert [c.args[0] for c in fake_bash.call_args_list] == [
        "uv run pytest -q",
        "uv run pytest tests/x.py",
    ]
    assert "always allow commands starting with uv run pytest in this project" in output
    assert "Commands starting with uv run pytest now run without asking" in output
    saved = load_permissions(permissions_file)
    assert saved.projects[str(cli.ROOT)].allowed_commands == ["uv run pytest"]


def test_always_is_not_offered_without_a_rule(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    fake_api["stream_response"].side_effect = [
        asks_for(bash_call("ls && ./build.sh")),
        StreamResult("thread-1"),
    ]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert confirm.call_args.kwargs["choices"] == ["y", "n"]
    assert "always allow" not in output


def test_yes_runs_command_once_without_saving(
    monkeypatch, console, fake_api, confirm, fake_bash, permissions_file
):
    confirm.return_value = "y"
    fake_api["stream_response"].side_effect = [
        asks_for(bash_call("make test")),
        StreamResult("thread-1"),
    ]

    run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    fake_bash.assert_called_once_with("make test", cli.ROOT)
    assert load_permissions(permissions_file).projects == {}


def test_broken_permissions_file_asks_for_every_command(
    monkeypatch, console, fake_api, confirm, fake_bash, permissions_file
):
    permissions_file.write_text("{broken")
    confirm.return_value = "y"
    fake_api["stream_response"].side_effect = [ASKS_FOR_LS, StreamResult("thread-1")]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert "Asking for every command, because" in output
    assert "isn't valid" in output
    assert confirm.call_args.kwargs["choices"] == ["y", "n"]
    fake_bash.assert_called_once_with("ls -la", cli.ROOT)
    # The broken file is left for the user to fix
    assert permissions_file.read_text() == "{broken"


def test_rule_that_cant_be_saved_still_runs_the_command(
    monkeypatch, console, fake_api, confirm, fake_bash
):
    confirm.return_value = "a"

    def fail(rule, project):
        raise cli.PermissionsError("can't save permissions.json: Read-only file system")

    monkeypatch.setattr(cli, "allow_in_project", fail)
    fake_api["stream_response"].side_effect = [
        asks_for(bash_call("make test")),
        StreamResult("thread-1"),
    ]

    output, _ = run(monkeypatch, console, [*SELECT_MODEL, "go", "exit"])

    assert "Couldn't save the rule: can't save permissions.json" in output
    fake_bash.assert_called_once_with("make test", cli.ROOT)


# --- Colours -----------------------------------------------------------------


@pytest.mark.parametrize("module", [cli, client])
@pytest.mark.parametrize(
    "text",
    ["Select one with /model <name>", "Max output tokens: 1024", "gpt-4.1"],
)
def test_text_is_not_highlighted_automatically(module, text):
    # Only colours set in markup, so the replies to every command look alike
    assert module.console.render_str(text).spans == []
