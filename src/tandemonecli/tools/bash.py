"""The ``bash`` tool: runs commands the model asks for on the user's machine.

The CLI sends ``BASH_TOOL_SPEC`` to SynapseAPI as a client tool. When the model calls
it, the API pauses the run and returns the call. The CLI asks the user for
permission, runs the command and sends the result back in a ``<bash_output>`` block.

Fallback for models without native tool calling (not used by the CLI at the moment):
the model writes a command in plain text instead,

    <bash>
    ls -la
    </bash>

with both tags on their own lines, so a tag mentioned in a sentence (for example
"there's no `<bash>` block here") isn't treated as a command. ``STOP_SEQUENCE`` stops
generation right after the command. Some models (such as Kimi) write a ``bash`` tool
call in their own native format as plain text; ``extract_tool_call_command`` reads
those.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tandemonecli.tools.sandbox import sandbox_command, sandbox_env, sandbox_problem

OPEN_TAG = "<bash>"
CLOSE_TAG = "</bash>"
# Only a closing tag at the start of a line ends generation, so mentioning the tag
# in a sentence doesn't cut the reply short
STOP_SEQUENCE = f"\n{CLOSE_TAG}"

# A tag counts only when it's alone on its line (apart from whitespace)
_OPEN_TAG_LINE = re.compile(rf"^[ \t]*{re.escape(OPEN_TAG)}[ \t]*$", re.MULTILINE)
_CLOSE_TAG_LINE = re.compile(rf"^[ \t]*{re.escape(CLOSE_TAG)}", re.MULTILINE)

# A native tool call written as text, for example:
#   <|open|>call tool="bash" index="1"<|sep|><|open|>argument key="command"
#   type="string"<|sep|>ls -la<|close|>argument<|sep|><|close|>call
# The command argument must belong to the same call, so the part between the call
# header and the argument can't contain the end of the call.
_TOOL_CALL_COMMAND = re.compile(
    r'<\|open\|>call tool="bash"[^<]*<\|sep\|>'
    r"(?:(?!<\|close\|>call).)*?"
    r'<\|open\|>argument key="command"[^<]*<\|sep\|>'
    r"(?P<command>.*?)<\|close\|>argument",
    re.DOTALL,
)

# Stop a command that runs for longer than this, so the CLI can't hang forever
DEFAULT_TIMEOUT = 120
# Longer output is cut off, so a huge result can't fill the model's context
MAX_OUTPUT_CHARS = 20_000

BASH_TOOL_NAME = "bash"
# The client tool sent to SynapseAPI, so the model can call it natively
BASH_TOOL_SPEC: dict[str, Any] = {
    "name": BASH_TOOL_NAME,
    "description": (
        "Runs a shell command with bash in the project directory on the user's "
        "machine, after the user approves it. Returns stdout, stderr and the exit "
        "code. Each call runs in a new shell, in a sandbox where only the project "
        "directory is writable and files outside it aren't visible. Commands can't "
        "read input and are "
        f"stopped after {DEFAULT_TIMEOUT} seconds; very long output is truncated."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to run."}
        },
        "required": ["command"],
    },
}


@dataclass(frozen=True)
class BashResult:
    """The result of running a command."""

    stdout: str
    stderr: str
    # None if the command didn't finish (it timed out or was interrupted)
    exit_code: int | None
    error: str | None = None


def extract_bash_command(text: str) -> str | None:
    """Return the command in the last ``<bash>`` block of a response, if any.

    The tags must be on their own lines; tags in the middle of a line are ignored.
    The closing tag is optional because the stop sequence usually removes it.
    Returns None if there is no block or the block is empty.
    """
    opening = None
    for opening in _OPEN_TAG_LINE.finditer(text):
        pass
    if opening is None:
        return None
    tail = text[opening.end() :]
    closing = _CLOSE_TAG_LINE.search(tail)
    if closing:
        tail = tail[: closing.start()]
    return tail.strip() or None


def extract_tool_call_command(text: str) -> str | None:
    """Return the command from a native ``bash`` tool call written in the reply.

    Only one command runs per reply, so if there are several calls the first one
    with a command is used. Returns None if there is no such call.
    """
    for call in _TOOL_CALL_COMMAND.finditer(text):
        if command := call["command"].strip():
            return command
    return None


def refused(problem: str) -> BashResult:
    """The result of a command that didn't run because the sandbox can't be used."""
    return BashResult(
        stdout="", stderr="", exit_code=None, error=f"Command not run: {problem}"
    )


def run_bash(command: str, root: Path, timeout: float = DEFAULT_TIMEOUT) -> BashResult:
    """Run a command with bash in a sandbox confined to ``root``, and capture its output.

    The command starts in ``root`` and can only change files there (see
    ``tandemonecli.tools.sandbox``). If the sandbox can't be used, the command
    doesn't run. Stdin is closed, so commands that wait for input fail instead of
    hanging.
    """
    if problem := sandbox_problem(root):
        return refused(problem)
    try:
        completed = subprocess.run(
            sandbox_command(root, command),
            env=sandbox_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return BashResult(
            stdout=_to_text(e.stdout),
            stderr=_to_text(e.stderr),
            exit_code=None,
            error=f"Command timed out after {timeout:g} seconds and was stopped.",
        )
    except KeyboardInterrupt:
        # subprocess.run kills the command before re-raising
        return BashResult(
            stdout="",
            stderr="",
            exit_code=None,
            error="Command was interrupted by the user (Ctrl+C).",
        )
    except OSError as e:
        return BashResult(stdout="", stderr="", exit_code=None, error=str(e))

    return BashResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        exit_code=completed.returncode,
    )


def _to_text(output: str | bytes | None) -> str:
    """Return partial output from a timed-out command as text."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Cut text down to ``limit`` characters, saying how much was left out."""
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n[... {omitted} more characters truncated]"


def format_bash_output(result: BashResult) -> str:
    """Format a result as the ``<bash_output>`` block the model expects."""
    parts = ["<bash_output>"]
    if result.stdout:
        parts.append(f"<stdout>\n{truncate(result.stdout).rstrip()}\n</stdout>")
    if result.stderr:
        parts.append(f"<stderr>\n{truncate(result.stderr).rstrip()}\n</stderr>")
    if result.error:
        parts.append(f"<error>{result.error}</error>")
    if result.exit_code is not None:
        parts.append(f"<exit_code>{result.exit_code}</exit_code>")
    parts.append("</bash_output>")
    return "\n".join(parts)


def format_declined() -> str:
    """Tell the model that the user chose not to run its command."""
    return (
        "<bash_output>\n"
        "<error>The user declined to run this command. It was not executed.</error>\n"
        "</bash_output>"
    )
