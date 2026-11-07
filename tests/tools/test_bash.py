import subprocess

import pytest

from tandemonecli.tools import bash
from tandemonecli.tools.bash import (
    BashResult,
    extract_bash_command,
    extract_tool_call_command,
    format_bash_output,
    format_declined,
    run_bash,
    truncate,
)

# --- extract_bash_command ----------------------------------------------------


def test_extracts_command_cut_off_by_stop_sequence():
    text = "I will list the files.\n<bash>\nls -la\n"

    assert extract_bash_command(text) == "ls -la"


def test_extracts_command_with_closing_tag():
    assert extract_bash_command("<bash>\ngit status\n</bash>") == "git status"


def test_extracts_multiline_command():
    text = "<bash>\ncat << 'EOF' > a.txt\nhello\nEOF\n"

    assert extract_bash_command(text) == "cat << 'EOF' > a.txt\nhello\nEOF"


def test_uses_last_bash_block():
    text = "<bash>\nls\n</bash>\nThen:\n<bash>\npwd\n"

    assert extract_bash_command(text) == "pwd"


def test_allows_indented_tags():
    text = "Next:\n  <bash>  \n  git status\n  </bash>\n"

    assert extract_bash_command(text) == "git status"


def test_ignores_inline_tag_after_a_real_block():
    text = "<bash>\nls\n</bash>\nThat's the only `<bash>` block I sent."

    assert extract_bash_command(text) == "ls"


def test_inline_closing_tag_does_not_end_command():
    text = "<bash>\necho '</bash>'\n"

    assert extract_bash_command(text) == "echo '</bash>'"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Just a normal answer.",
        "<bash>\n   \n",
        "<bash></bash>",
        "<bash>ls</bash>",
        "<bash_output>\n<stdout>hi</stdout>\n</bash_output>",
        # Tags mentioned in prose must not be run as commands
        "My last message contained no `<bash>` block. The backtick error suggests...",
        "When the model replies with a `<bash>...",
        "Write <bash> on its own line,\nthen the command.",
    ],
)
def test_returns_none_without_a_command(text):
    assert extract_bash_command(text) is None


# --- extract_tool_call_command -----------------------------------------------

# Copied from a real Kimi K3 reply
KIMI_CALL = (
    '<|open|>tools<|sep|><|open|>call tool="bash" index="1"<|sep|>'
    '<|open|>argument key="command" type="string"<|sep|>'
    "ls -la && git status 2>/dev/null | head -5<|close|>argument<|sep|>"
    "<|close|>call<|sep|><|close|>tools<|sep|><|close|>message<|sep|>"
)


def native_call(command: str, tool: str = "bash", key: str = "command") -> str:
    return (
        f'<|open|>call tool="{tool}" index="1"<|sep|>'
        f'<|open|>argument key="{key}" type="string"<|sep|>'
        f"{command}<|close|>argument<|sep|><|close|>call<|sep|>"
    )


def test_extracts_command_from_kimi_tool_call():
    text = f"I'll check the files.\n{KIMI_CALL}"

    assert (
        extract_tool_call_command(text) == "ls -la && git status 2>/dev/null | head -5"
    )


def test_tool_call_keeps_multiline_command():
    text = native_call("cat << 'EOF' > a.txt\nhello\nEOF\n")

    assert extract_tool_call_command(text) == "cat << 'EOF' > a.txt\nhello\nEOF"


def test_uses_first_of_several_tool_calls():
    text = native_call("ls") + native_call("pwd")

    assert extract_tool_call_command(text) == "ls"


def test_command_argument_must_belong_to_the_bash_call():
    # The bash call has no command argument; the next call's argument isn't used
    text = (
        '<|open|>call tool="bash" index="1"<|sep|><|close|>call<|sep|>'
        + native_call("rm -rf x", tool="other")
    )

    assert extract_tool_call_command(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Just a normal answer.",
        native_call("ls", tool="python"),
        native_call("ls", key="path"),
        native_call("   "),
        # Cut off before the argument was finished
        '<|open|>call tool="bash" index="1"<|sep|>'
        '<|open|>argument key="command" type="string"<|sep|>rm -',
        "<bash>\nls\n",
    ],
)
def test_tool_call_returns_none_without_a_command(text):
    assert extract_tool_call_command(text) is None


# --- run_bash ----------------------------------------------------------------


def test_run_bash_captures_stdout_and_exit_code():
    result = run_bash("echo hello")

    assert result == BashResult(stdout="hello\n", stderr="", exit_code=0)


def test_run_bash_captures_stderr_and_failure():
    result = run_bash("echo oops >&2; exit 3")

    assert result.stderr == "oops\n"
    assert result.exit_code == 3


def test_run_bash_uses_bash():
    # [[ ]] is bash syntax that plain sh doesn't support
    assert run_bash("[[ 1 == 1 ]] && echo yes").stdout == "yes\n"


def test_run_bash_closes_stdin():
    # read fails at once instead of waiting for input
    result = run_bash("read line", timeout=5)

    assert result.exit_code == 1


def test_run_bash_reports_timeout():
    result = run_bash("echo started; sleep 5", timeout=0.5)

    assert result.exit_code is None
    assert result.error is not None
    assert "timed out after 0.5 seconds" in result.error


def test_run_bash_reports_interrupt(monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(bash.subprocess, "run", interrupted)

    result = run_bash("sleep 100")

    assert result.exit_code is None
    assert result.error is not None
    assert "interrupted" in result.error


def test_run_bash_reports_missing_bash(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("No such file or directory: 'bash'")

    monkeypatch.setattr(bash.subprocess, "run", missing)

    result = run_bash("ls")

    assert result.exit_code is None
    assert result.error == "No such file or directory: 'bash'"


def test_run_bash_decodes_partial_output_after_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("bash", 1, output=b"partial", stderr=None)

    monkeypatch.setattr(bash.subprocess, "run", timeout)

    result = run_bash("sleep 100", timeout=1)

    assert result.stdout == "partial"
    assert result.stderr == ""


# --- Formatting --------------------------------------------------------------


def test_format_bash_output_matches_system_prompt_format():
    result = BashResult(stdout="index.js\n", stderr="", exit_code=0)

    assert format_bash_output(result) == (
        "<bash_output>\n<stdout>\nindex.js\n</stdout>\n<exit_code>0</exit_code>\n"
        "</bash_output>"
    )


def test_format_bash_output_includes_stderr():
    result = BashResult(stdout="", stderr="not found\n", exit_code=127)

    assert format_bash_output(result) == (
        "<bash_output>\n<stderr>\nnot found\n</stderr>\n<exit_code>127</exit_code>\n"
        "</bash_output>"
    )


def test_format_bash_output_reports_error_without_exit_code():
    result = BashResult(stdout="", stderr="", exit_code=None, error="Timed out.")

    assert format_bash_output(result) == (
        "<bash_output>\n<error>Timed out.</error>\n</bash_output>"
    )


def test_format_bash_output_truncates_long_output():
    result = BashResult(
        stdout="x" * (bash.MAX_OUTPUT_CHARS + 5), stderr="", exit_code=0
    )

    formatted = format_bash_output(result)

    assert "[... 5 more characters truncated]" in formatted
    assert len(formatted) < bash.MAX_OUTPUT_CHARS + 200


def test_truncate_leaves_short_text_alone():
    assert truncate("short", limit=10) == "short"


def test_format_declined_says_command_was_not_run():
    declined = format_declined()

    assert declined.startswith("<bash_output>")
    assert "declined" in declined
    assert "not executed" in declined


# --- BASH_TOOL_SPEC ----------------------------------------------------------


def test_tool_spec_takes_a_required_command():
    spec = bash.BASH_TOOL_SPEC

    assert spec["name"] == bash.BASH_TOOL_NAME == "bash"
    assert spec["input_schema"]["type"] == "object"
    assert spec["input_schema"]["properties"]["command"]["type"] == "string"
    assert spec["input_schema"]["required"] == ["command"]


def test_tool_spec_states_the_timeout():
    assert f"{bash.DEFAULT_TIMEOUT} seconds" in bash.BASH_TOOL_SPEC["description"]
