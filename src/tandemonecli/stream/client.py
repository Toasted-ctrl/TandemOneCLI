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
CLIENT_TOOLS = [BASH_TOOL_SPEC]
CLIENT_TOOL_NAMES = frozenset(spec["name"] for spec in CLIENT_TOOLS)

console = Console()


# Longest tool input or error shown in a server tool line, in characters
MAX_DETAIL_LENGTH = 120


def show_server_tool(activity: "ServerToolActivity") -> None:
    """Show that SynapseAPI itself is using a tool, such as reading a web page."""
    label = safe_markup(activity.name.replace("_", " "))
    detail = safe_markup(activity.detail)
    if activity.status == "started":
        line = f"[bold cyan]⟳ {label}[/bold cyan]"
        console.print(f"{line} {detail}" if detail else line)
    elif activity.status == "error":
        line = f"[bold red]✗ {label} failed[/bold red]"
        console.print(f"{line}: {detail}" if detail else line)
    else:
        console.print(f"[bold green]✓ {label}[/bold green]")


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
class ServerToolActivity:
    """A tool that SynapseAPI runs itself, such as web search, starting or done."""

    name: str
    # "started", "success" or "error"
    status: str
    # For a start, the tool's input (such as the URL); for an error, the reason
    detail: str = ""


def _shorten(text: str) -> str:
    """Return the first non-blank line of text, cut to MAX_DETAIL_LENGTH."""
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(line) > MAX_DETAIL_LENGTH:
        return line[: MAX_DETAIL_LENGTH - 1] + "…"
    return line


def _summarize_args(args: Any) -> str:
    """Return the most telling part of a tool's input, such as its URL or query."""
    if not isinstance(args, dict):
        return ""
    for key in ("query", "url", "urls"):
        value = args.get(key)
        if isinstance(value, str):
            return _shorten(value)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return _shorten(", ".join(value))
    return _shorten(json.dumps(args, ensure_ascii=False)) if args else ""


def _update_messages(event: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield the messages in an ``update`` event, from every agent step in it.

    The data of an update maps each step that finished (such as ``model`` or
    ``tools``) to its output; the messages in it are serialized LangChain messages.
    """
    if event.get("type") != "update":
        return
    for step_output in event["data"].values():
        if not isinstance(step_output, dict):
            continue
        messages = step_output.get("messages")
        if isinstance(messages, list):
            yield from (m for m in messages if isinstance(m, dict))


def extract_server_tools(
    event: dict[str, Any], client_tool_names: Iterable[str] = ()
) -> list[ServerToolActivity]:
    """Return the server-side tool calls and results in an ``update`` event.

    Server-side tools, such as web search, run inside SynapseAPI, so the CLI
    doesn't get a ``tool_request`` for them. A finished model step lists the tool
    calls the model made (with their full input); a finished tool step has their
    results, with a status of "success" or "error". Calls of client tools are
    skipped, as the CLI shows those itself.
    """
    skipped = set(client_tool_names)
    activity: list[ServerToolActivity] = []
    for message in _update_messages(event):
        message_type = message.get("type")
        if message_type == "ai" and isinstance(message.get("tool_calls"), list):
            for call in message["tool_calls"]:
                if not isinstance(call, dict):
                    continue
                name = call.get("name")
                if isinstance(name, str) and name not in skipped:
                    activity.append(
                        ServerToolActivity(
                            name, "started", _summarize_args(call.get("args"))
                        )
                    )
        elif message_type == "tool":
            name = message.get("name")
            if not isinstance(name, str) or name in skipped:
                continue
            if message.get("status") == "error":
                content = message.get("content")
                reason = _shorten(content) if isinstance(content, str) else ""
                activity.append(ServerToolActivity(name, "error", reason))
            else:
                activity.append(ServerToolActivity(name, "success"))
    return activity


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
    web_search: bool = False,
    thread_id: str | None = None,
    tool_results: Sequence[ToolResult] | None = None,
    max_output_tokens: int | None = None,
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
        max_output_tokens: Most tokens the model may generate, or None for the
            model's default

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
        "client_tools": CLIENT_TOOLS,
        "tools": {"web_search": web_search},
    }
    if tool_results is not None:
        payload["tool_results"] = [asdict(result) for result in tool_results]
    else:
        payload["prompt"] = prompt
    if thread_id:
        payload["thread_id"] = thread_id
    if max_output_tokens is not None:
        payload["model"]["parameters"] = {"max_output_tokens": max_output_tokens}

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
            timeout=300,
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

                elif server_tools := extract_server_tools(
                    event, client_tool_names=CLIENT_TOOL_NAMES
                ):
                    # Print below what's shown of the reply so far
                    status.stop()
                    printer.pause()
                    for activity in server_tools:
                        show_server_tool(activity)
                    # Keep the spinner going while the tools run or the model reads
                    # their results
                    status.start()

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
