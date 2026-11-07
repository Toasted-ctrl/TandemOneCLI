from collections.abc import Iterable
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from rich.console import Console

from tandemonecli import cli
from tandemonecli.permissions import Permissions, load_permissions, save_permissions
from tandemonecli.startup.models import AvailableModels
from tandemonecli.startup.user import User
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


def test_warns_at_startup_when_sandbox_is_unavailable(monkeypatch, console, fake_api):
    fake_api["sandbox_problem"].return_value = "bwrap isn't installed"

    output, _ = run(monkeypatch, console, ["exit"])

    assert "The model can't run commands here: bwrap isn't installed" in output
    assert "Commands run in a sandbox" not in output


def test_line_is_drawn_above_every_prompt(monkeypatch, console, fake_api):
    output, prompt = run(monkeypatch, console, ["/help", "exit"])

    rules = [line for line in output.splitlines() if set(line) == {"─"}]
    assert len(rules) == len(prompt.prompts) == 2
    assert output.rstrip().splitlines()[-2] == rules[-1]


def test_greets_user_and_lists_providers_only(monkeypatch, console, fake_api):
    output, _ = run(monkeypatch, console, ["exit"])

    assert "Hello Ada! Welcome to Tandem One CLI!" in output
    assert f"Working in {cli.ROOT}" in output
    assert "Commands run in a sandbox: only this directory can be changed." in output
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
        tool_results=None,
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


def test_prompt_escapes_provider_and_model_names(monkeypatch, console, fake_api):
    fake_api["get_models"].return_value = AvailableModels(providers={"[x]": ["m[b]"]})

    _, fake_prompt = run(monkeypatch, console, ["/provider [x]", "/model m[b]", "exit"])

    assert "\\[x]" in fake_prompt.prompts[1]
    assert "m\\[b]" in fake_prompt.prompts[2]


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
