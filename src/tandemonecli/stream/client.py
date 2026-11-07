"""Client for the SynapseAPI streaming inference endpoint."""

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import requests
from rich.console import Console

from tandemonecli.config.settings import tocli_settings
from tandemonecli.prompts.system import SYSTEM_PROMPT
from tandemonecli.stream.markdown import MarkdownPrinter
from tandemonecli.text import safe_markup
from tandemonecli.tools.bash import BASH_TOOL_SPEC

STREAM_PATH = "/api/v1/inference/conversation/stream"
STATUS_MESSAGE = "[bold green]Thinking..."

console = Console()


def iter_sse_data(lines: Iterable[str]) -> Iterator[str]:
    """Yield the data payload of each server-sent event.

    An event can span several ``data:`` lines, which are joined with newlines.
    A blank line ends the event. Comments (``:``) and other fields are ignored.
    """
    data_lines: list[str] = []
    for line in lines:
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue

        if line.startswith(":"):
            continue

        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value.removeprefix(" "))

    # Flush the last event if the stream ended without a blank line
    if data_lines:
        yield "\n".join(data_lines)


def parse_event(payload: str) -> dict[str, Any] | None:
    """Parse an event payload as JSON, returning None if it isn't an event object."""
    try:
        event: Any = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
        return None
    return event


def iter_events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Yield the parsed events from a stream of SSE lines, skipping invalid ones."""
    for payload in iter_sse_data(lines):
        event = parse_event(payload)
        if event is not None:
            yield event


def _data_str(event: dict[str, Any], event_type: str, key: str) -> str | None:
    """Return ``event["data"][key]`` if the event has the given type and it's a str."""
    if event.get("type") != event_type:
        return None
    value = event["data"].get(key)
    return value if isinstance(value, str) else None


def extract_content(payload: str) -> str | None:
    """Return the message text from an event payload, or None if there is none."""
    event = parse_event(payload)
    return _data_str(event, "message", "content") if event else None


def iter_content(lines: Iterable[str]) -> Iterator[str]:
    """Yield the message text chunks from a stream of SSE lines."""
    for event in iter_events(lines):
        content = _data_str(event, "message", "content")
        if content:
            yield content


@dataclass(frozen=True)
class ToolCall:
    """A call of a client tool the model made, waiting for its result."""

    tool_call_id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """The result of a tool call, sent back to continue the model's run."""

    tool_call_id: str
    content: str
    # True if the tool failed or the user declined to run it
    is_error: bool = False


def extract_tool_calls(event: dict[str, Any]) -> list[ToolCall]:
    """Return the tool calls of a ``tool_request`` event, skipping invalid ones."""
    if event.get("type") != "tool_request":
        return []
    calls = event["data"].get("tool_calls")
    if not isinstance(calls, list):
        return []

    tool_calls: list[ToolCall] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        tool_call_id, name, args = (
            call.get("tool_call_id"),
            call.get("name"),
            call.get("args"),
        )
        if isinstance(tool_call_id, str) and isinstance(name, str):
            tool_calls.append(
                ToolCall(tool_call_id, name, args if isinstance(args, dict) else {})
            )
    return tool_calls


@dataclass(frozen=True)
class StreamResult:
    """The outcome of a streamed response."""

    # ID to send with the next message to continue the conversation,
    # or None if the next message should start a new conversation
    thread_id: str | None
    # The full text of the model's reply, as received (control chars not stripped)
    text: str = ""
    # True if the request failed or the stream reported an error, so the text
    # may be incomplete
    failed: bool = False
    # Tool calls the model is waiting on; send their results to continue
    tool_calls: tuple[ToolCall, ...] = ()


def _is_invalid_thread(error: requests.HTTPError) -> bool:
    """Return True if SynapseAPI rejected the request because of an unknown thread."""
    response = error.response
    if response is None or response.status_code != 404:
        return False
    try:
        return response.json().get("detail") == "Invalid thread_id"
    except ValueError, AttributeError:
        return False


def _is_waiting_for_tool_results(error: requests.HTTPError) -> bool:
    """Return True if SynapseAPI rejected a message because the model is still
    waiting for tool results."""
    response = error.response
    return response is not None and response.status_code == 409


def stream_response(
    api_key: str,
    prompt: str | None,
    provider: str,
    model_name: str,
    thread_id: str | None = None,
    tool_results: Sequence[ToolResult] | None = None,
) -> StreamResult:
    """Stream an inference response to the terminal as it arrives.

    Args:
        api_key: API key for authentication
        prompt: The user's input prompt, or None when sending tool results
        provider: The model provider (e.g., "openai")
        model_name: The model name (e.g., "gpt-4")
        thread_id: ID of the conversation to continue, or None to start a new one
        tool_results: Results of the tool calls the model is waiting on, sent
            instead of a prompt to continue its run (requires thread_id)

    Returns:
        The thread ID for the next message (None to start a new conversation),
        the full text of the reply and the tool calls the model is waiting on.
    """
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    payload: dict[str, Any] = {
        "model": {
            "provider": provider,
            "name": model_name,
        },
        "system_prompt": SYSTEM_PROMPT,
        # Sent with every request, also with tool results, so the model keeps them
        "client_tools": [BASH_TOOL_SPEC],
    }
    if tool_results is not None:
        payload["tool_results"] = [asdict(result) for result in tool_results]
    else:
        payload["prompt"] = prompt
    if thread_id:
        payload["thread_id"] = thread_id

    # Show a spinner until the first chunk of text arrives
    status = console.status(STATUS_MESSAGE, spinner="dots")
    status.start()
    printer = MarkdownPrinter(console)
    chunks: list[str] = []
    tool_calls: list[ToolCall] = []
    failed = False

    def end_output() -> None:
        """Stop the spinner and print the rest of the reply."""
        status.stop()
        printer.finish()

    try:
        with requests.post(
            f"{tocli_settings.SYNAPSE_API_URL}{STREAM_PATH}",
            headers=headers,
            json=payload,
            stream=True,
            timeout=30,
        ) as response:
            response.raise_for_status()
            # SSE is always UTF-8, but requests defaults text/* to ISO-8859-1
            response.encoding = "utf-8"
            # The API sends the thread ID in a header and in the first event
            thread_id = response.headers.get("X-Thread-ID") or thread_id

            lines = (
                line if isinstance(line, str) else line.decode("utf-8")
                for line in response.iter_lines(decode_unicode=True)
            )
            for event in iter_events(lines):
                if new_thread_id := _data_str(event, "thread", "thread_id"):
                    thread_id = new_thread_id

                elif event.get("type") == "tool_request":
                    tool_calls += extract_tool_calls(event)

                elif detail := _data_str(event, "error", "detail"):
                    failed = True
                    end_output()
                    console.print(
                        f"[red]Error from SynapseAPI:[/red] {safe_markup(detail)}"
                    )

                elif content := _data_str(event, "message", "content"):
                    chunks.append(content)
                    # The spinner and the reply can't be shown live at once
                    status.stop()
                    printer.add(content)

            end_output()

    except requests.HTTPError as e:
        failed = True
        end_output()
        if thread_id and _is_invalid_thread(e):
            console.print(
                "[red]This conversation could not be found.[/red] "
                "Your next message will start a new conversation."
            )
            return StreamResult(thread_id=None, text="".join(chunks), failed=True)
        if _is_waiting_for_tool_results(e):
            console.print(
                "[red]The model is still waiting for the result of a command.[/red] "
                "Use /new to start a new conversation."
            )
        else:
            console.print(f"[red]Error streaming response: {safe_markup(str(e))}[/red]")

    except requests.exceptions.RequestException as e:
        failed = True
        end_output()
        console.print(f"[red]Error streaming response: {safe_markup(str(e))}[/red]")

    finally:
        # Also on Ctrl+C; stopping and finishing twice is harmless
        end_output()

    return StreamResult(
        thread_id=thread_id,
        text="".join(chunks),
        failed=failed,
        tool_calls=tuple(tool_calls),
    )
