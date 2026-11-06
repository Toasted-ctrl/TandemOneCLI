"""Client for the SynapseAPI streaming inference endpoint."""

import json
from collections.abc import Iterable, Iterator
from typing import Any

import requests
from rich.console import Console

from tandemonecli.config.settings import tocli_settings
from tandemonecli.text import safe_markup, strip_control_chars

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


def _is_invalid_thread(error: requests.HTTPError) -> bool:
    """Return True if SynapseAPI rejected the request because of an unknown thread."""
    response = error.response
    if response is None or response.status_code != 404:
        return False
    try:
        return response.json().get("detail") == "Invalid thread_id"
    except ValueError, AttributeError:
        return False


def stream_response(
    api_key: str,
    prompt: str,
    provider: str,
    model_name: str,
    thread_id: str | None = None,
) -> str | None:
    """Stream an inference response to the terminal as it arrives.

    Args:
        api_key: API key for authentication
        prompt: The user's input prompt
        provider: The model provider (e.g., "openai")
        model_name: The model name (e.g., "gpt-4")
        thread_id: ID of the conversation to continue, or None to start a new one

    Returns:
        The thread ID to send with the next message to continue the conversation,
        or None if the next message should start a new conversation.
    """
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    payload: dict[str, Any] = {
        "prompt": prompt,
        "model": {
            "provider": provider,
            "name": model_name,
        },
    }
    if thread_id:
        payload["thread_id"] = thread_id

    # Show a spinner until the first chunk of text arrives
    status = console.status(STATUS_MESSAGE, spinner="dots")
    status.start()
    has_output = False

    def end_output_line() -> None:
        """Stop the spinner and move to a new line if text was printed."""
        nonlocal has_output
        status.stop()
        if has_output:
            console.print()
            has_output = False

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

                elif detail := _data_str(event, "error", "detail"):
                    end_output_line()
                    console.print(
                        f"[red]Error from SynapseAPI:[/red] {safe_markup(detail)}"
                    )

                elif content := _data_str(event, "message", "content"):
                    if not has_output:
                        status.stop()
                        has_output = True
                    # markup=False so model output like "[bold]" is printed as-is
                    # Strip escape sequences so output can't control the terminal
                    console.print(
                        strip_control_chars(content),
                        end="",
                        markup=False,
                        highlight=False,
                        soft_wrap=True,
                    )

            end_output_line()

    except requests.HTTPError as e:
        end_output_line()
        if thread_id and _is_invalid_thread(e):
            console.print(
                "[red]This conversation could not be found.[/red] "
                "Your next message will start a new conversation."
            )
            return None
        console.print(f"[red]Error streaming response: {safe_markup(str(e))}[/red]")

    except requests.exceptions.RequestException as e:
        end_output_line()
        console.print(f"[red]Error streaming response: {safe_markup(str(e))}[/red]")

    finally:
        # Also stops the spinner on Ctrl+C; stopping twice is harmless
        status.stop()

    return thread_id
