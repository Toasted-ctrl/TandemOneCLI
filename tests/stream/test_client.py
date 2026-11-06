import json
from unittest.mock import MagicMock

import pytest
import requests
from rich.console import Console

from tandemonecli.stream import client
from tandemonecli.stream.client import (
    _is_invalid_thread,
    extract_content,
    iter_content,
    iter_events,
    iter_sse_data,
    parse_event,
    stream_response,
)

METADATA = {
    "ls_provider": "openai",
    "ls_model_name": "kimi-k3",
    "ls_temperature": None,
    "lc_versions": {"langchain-core": "1.7.0"},
}


def message_event(content: str) -> str:
    """Build an SSE line in the format SynapseAPI sends."""
    payload = {"type": "message", "data": {"content": content, "metadata": METADATA}}
    return f"data: {json.dumps(payload)}"


def sse_lines(*contents: str) -> list[str]:
    """Build SSE lines for several message events, separated by blank lines."""
    lines: list[str] = []
    for content in contents:
        lines += [message_event(content), ""]
    return lines


# --- iter_sse_data -----------------------------------------------------------


def test_sse_yields_one_payload_per_event():
    lines = ["data: one", "", "data: two", ""]

    assert list(iter_sse_data(lines)) == ["one", "two"]


def test_sse_joins_multiline_data():
    lines = ["data: first", "data: second", ""]

    assert list(iter_sse_data(lines)) == ["first\nsecond"]


def test_sse_ignores_comments_and_other_fields():
    lines = [": keep-alive", "event: message", "id: 1", "data: payload", ""]

    assert list(iter_sse_data(lines)) == ["payload"]


def test_sse_accepts_data_without_space():
    assert list(iter_sse_data(["data:payload", ""])) == ["payload"]


def test_sse_flushes_last_event_without_trailing_blank_line():
    assert list(iter_sse_data(["data: last"])) == ["last"]


def test_sse_skips_extra_blank_lines():
    assert list(iter_sse_data(["", "", "data: x", "", ""])) == ["x"]


# --- extract_content ---------------------------------------------------------


def test_extracts_content_from_message_event():
    payload = message_event(" you").removeprefix("data: ")

    assert extract_content(payload) == " you"


def test_extracts_content_with_escaped_characters():
    payload = json.dumps(
        {"type": "message", "data": {"content": 'say "hi"\ncafé \\o/'}}
    )

    assert extract_content(payload) == 'say "hi"\ncafé \\o/'


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[DONE]",
        '"just a string"',
        '{"type": "metadata", "data": {"content": "x"}}',
        '{"type": "message"}',
        '{"type": "message", "data": "oops"}',
        '{"type": "message", "data": {"content": 42}}',
    ],
)
def test_returns_none_for_non_message_payloads(payload):
    assert extract_content(payload) is None


# --- iter_content ------------------------------------------------------------


def test_iter_content_yields_text_chunks_in_order():
    lines = sse_lines(" you", " with", " today", "?")

    assert "".join(iter_content(lines)) == " you with today?"


def test_iter_content_skips_empty_and_invalid_events():
    lines = [*sse_lines("a", ""), "data: garbage", "", *sse_lines("b")]

    assert list(iter_content(lines)) == ["a", "b"]


# --- stream_response ---------------------------------------------------------


@pytest.fixture
def console(monkeypatch) -> Console:
    recording = Console(record=True, width=80, force_terminal=False)
    monkeypatch.setattr(client, "console", recording)
    return recording


def fake_post(
    monkeypatch,
    lines: list[str],
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    error_body: dict | None = None,
) -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = status_code
    response.headers = headers or {}
    response.iter_lines.return_value = iter(lines)
    response.json.return_value = error_body or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} Error", response=response
        )
    post = MagicMock(return_value=response)
    monkeypatch.setattr(client.requests, "post", post)
    return post


def test_stream_response_prints_content(monkeypatch, console):
    fake_post(monkeypatch, sse_lines("Hello", " there", "!"))

    stream_response("key", "hi", "openai", "kimi-k3")

    assert console.export_text() == "Hello there!\n"


def test_stream_response_does_not_interpret_markup(monkeypatch, console):
    fake_post(monkeypatch, sse_lines("use [bold]this[/bold]"))

    stream_response("key", "hi", "openai", "kimi-k3")

    assert console.export_text() == "use [bold]this[/bold]\n"


def test_stream_response_sends_expected_request(monkeypatch, console):
    post = fake_post(monkeypatch, [])

    stream_response("secret-key", "hello", "openai", "kimi-k3")

    post.assert_called_once_with(
        "https://synapse.test/api/v1/inference/conversation/stream",
        headers={
            "X-API-Key": "secret-key",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        json={"prompt": "hello", "model": {"provider": "openai", "name": "kimi-k3"}},
        stream=True,
        timeout=30,
    )


def test_stream_response_decodes_as_utf8(monkeypatch, console):
    post = fake_post(monkeypatch, [])

    stream_response("key", "hi", "openai", "kimi-k3")

    response = post.return_value
    assert response.encoding == "utf-8"
    response.iter_lines.assert_called_once_with(decode_unicode=True)


def test_stream_response_prints_http_error(monkeypatch, console):
    fake_post(monkeypatch, [], status_code=401)

    stream_response("bad-key", "hi", "openai", "kimi-k3")

    assert "Error streaming response: 401 Error" in console.export_text()


def test_stream_response_prints_connection_error(monkeypatch, console):
    monkeypatch.setattr(
        client.requests,
        "post",
        MagicMock(side_effect=requests.ConnectionError("unreachable")),
    )

    stream_response("key", "hi", "openai", "kimi-k3")

    assert "Error streaming response: unreachable" in console.export_text()


# --- Spinner -----------------------------------------------------------------


class SpinnerSpy:
    """Records when the spinner starts and stops relative to printed output."""

    def __init__(self, monkeypatch, console: Console):
        self.events: list[str] = []
        status = MagicMock()
        status.start.side_effect = lambda: self.events.append("start")
        status.stop.side_effect = lambda: self.events.append("stop")
        self.status_call = MagicMock(return_value=status)
        monkeypatch.setattr(console, "status", self.status_call)

        original_print = console.print

        def recording_print(*args, **kwargs):
            if args:
                self.events.append(f"print:{args[0]}")
            original_print(*args, **kwargs)

        monkeypatch.setattr(console, "print", recording_print)


@pytest.fixture
def spinner(monkeypatch, console) -> SpinnerSpy:
    return SpinnerSpy(monkeypatch, console)


def test_spinner_starts_before_request(monkeypatch, console, spinner):
    post = fake_post(monkeypatch, sse_lines("Hi"))
    post.side_effect = lambda *a, **kw: (
        spinner.events.append("request"),
        post.return_value,
    )[1]

    stream_response("key", "hi", "openai", "kimi-k3")

    assert spinner.events.index("start") < spinner.events.index("request")
    spinner.status_call.assert_called_once_with(client.STATUS_MESSAGE, spinner="dots")


def test_spinner_stops_before_first_text(monkeypatch, console, spinner):
    fake_post(monkeypatch, sse_lines("Hello", " there"))

    stream_response("key", "hi", "openai", "kimi-k3")

    assert spinner.events.index("stop") < spinner.events.index("print:Hello")
    assert console.export_text() == "Hello there\n"


def test_spinner_stops_when_stream_has_no_text(monkeypatch, console, spinner):
    fake_post(monkeypatch, [])

    stream_response("key", "hi", "openai", "kimi-k3")

    assert "stop" in spinner.events
    assert console.export_text() == ""


def test_spinner_stops_before_error_message(monkeypatch, console, spinner):
    fake_post(monkeypatch, [], status_code=500)

    stream_response("key", "hi", "openai", "kimi-k3")

    first_print = next(e for e in spinner.events if e.startswith("print:"))
    assert spinner.events.index("stop") < spinner.events.index(first_print)
    assert console.export_text() == "Error streaming response: 500 Error\n"


def test_error_mid_stream_starts_on_new_line(monkeypatch, console, spinner):
    def broken_stream():
        yield from sse_lines("partial")
        raise requests.ConnectionError("dropped")

    post = fake_post(monkeypatch, [])
    post.return_value.iter_lines.return_value = broken_stream()

    stream_response("key", "hi", "openai", "kimi-k3")

    assert console.export_text() == "partial\nError streaming response: dropped\n"


def test_spinner_stops_on_ctrl_c(monkeypatch, console, spinner):
    def interrupted_stream():
        yield from sse_lines("partial")
        raise KeyboardInterrupt

    post = fake_post(monkeypatch, [])
    post.return_value.iter_lines.return_value = interrupted_stream()

    with pytest.raises(KeyboardInterrupt):
        stream_response("key", "hi", "openai", "kimi-k3")

    assert spinner.events[-1] == "stop"


def test_spinner_stops_on_ctrl_c_before_first_text(monkeypatch, console, spinner):
    monkeypatch.setattr(
        client.requests, "post", MagicMock(side_effect=KeyboardInterrupt)
    )

    with pytest.raises(KeyboardInterrupt):
        stream_response("key", "hi", "openai", "kimi-k3")

    assert spinner.events == ["start", "stop"]


# --- Threads -----------------------------------------------------------------

THREAD_A = "11111111-1111-4111-8111-111111111111"
THREAD_B = "22222222-2222-4222-8222-222222222222"


def thread_event(thread_id: str) -> list[str]:
    payload = {"type": "thread", "data": {"thread_id": thread_id}}
    return [f"data: {json.dumps(payload)}", ""]


def error_event(detail: str) -> list[str]:
    payload = {"type": "error", "data": {"detail": detail}}
    return [f"data: {json.dumps(payload)}", ""]


def sent_payload(post: MagicMock) -> dict:
    return post.call_args.kwargs["json"]


def test_new_conversation_does_not_send_thread_id(monkeypatch, console):
    post = fake_post(monkeypatch, [])

    stream_response("key", "hi", "openai", "kimi-k3")

    assert "thread_id" not in sent_payload(post)


def test_existing_conversation_sends_thread_id(monkeypatch, console):
    post = fake_post(monkeypatch, [])

    stream_response("key", "hi", "openai", "kimi-k3", thread_id=THREAD_A)

    assert sent_payload(post)["thread_id"] == THREAD_A


def test_returns_thread_id_from_thread_event(monkeypatch, console):
    fake_post(monkeypatch, [*thread_event(THREAD_A), *sse_lines("Hi")])

    assert stream_response("key", "hi", "openai", "kimi-k3") == THREAD_A
    assert console.export_text() == "Hi\n"


def test_returns_thread_id_from_header(monkeypatch, console):
    fake_post(monkeypatch, sse_lines("Hi"), headers={"X-Thread-ID": THREAD_A})

    assert stream_response("key", "hi", "openai", "kimi-k3") == THREAD_A


def test_thread_event_takes_precedence_over_header(monkeypatch, console):
    fake_post(monkeypatch, thread_event(THREAD_B), headers={"X-Thread-ID": THREAD_A})

    assert stream_response("key", "hi", "openai", "kimi-k3") == THREAD_B


def test_returns_given_thread_id_when_server_sends_none(monkeypatch, console):
    fake_post(monkeypatch, sse_lines("Hi"))

    result = stream_response("key", "hi", "openai", "kimi-k3", thread_id=THREAD_A)

    assert result == THREAD_A


def test_returns_none_for_new_conversation_without_thread(monkeypatch, console):
    fake_post(monkeypatch, sse_lines("Hi"))

    assert stream_response("key", "hi", "openai", "kimi-k3") is None


def test_keeps_thread_id_on_connection_error(monkeypatch, console):
    monkeypatch.setattr(
        client.requests,
        "post",
        MagicMock(side_effect=requests.ConnectionError("unreachable")),
    )

    result = stream_response("key", "hi", "openai", "kimi-k3", thread_id=THREAD_A)

    assert result == THREAD_A


def test_keeps_thread_id_on_other_http_errors(monkeypatch, console):
    fake_post(monkeypatch, [], status_code=500)

    result = stream_response("key", "hi", "openai", "kimi-k3", thread_id=THREAD_A)

    assert result == THREAD_A


def test_invalid_thread_starts_new_conversation(monkeypatch, console):
    fake_post(
        monkeypatch,
        [],
        status_code=404,
        error_body={"detail": "Invalid thread_id"},
    )

    result = stream_response("key", "hi", "openai", "kimi-k3", thread_id=THREAD_A)

    output = " ".join(console.export_text().split())
    assert result is None
    assert "could not be found" in output
    assert "next message will start a new conversation" in output


def test_other_404_keeps_thread_id(monkeypatch, console):
    fake_post(monkeypatch, [], status_code=404, error_body={"detail": "Not Found"})

    result = stream_response("key", "hi", "openai", "kimi-k3", thread_id=THREAD_A)

    assert result == THREAD_A
    assert "Error streaming response: 404 Error" in console.export_text()


def test_404_with_non_json_body_keeps_thread_id(monkeypatch, console):
    post = fake_post(monkeypatch, [], status_code=404)
    post.return_value.json.side_effect = ValueError("not json")

    result = stream_response("key", "hi", "openai", "kimi-k3", thread_id=THREAD_A)

    assert result == THREAD_A


# --- Error events ------------------------------------------------------------


def test_error_event_is_printed(monkeypatch, console):
    fake_post(monkeypatch, error_event("Unable to connect to Provider"))

    stream_response("key", "hi", "openai", "kimi-k3")

    assert console.export_text() == (
        "Error from SynapseAPI: Unable to connect to Provider\n"
    )


def test_error_event_after_text_starts_on_new_line(monkeypatch, console):
    fake_post(
        monkeypatch,
        [*sse_lines("partial"), *error_event("Agent run failed unexpectedly")],
    )

    stream_response("key", "hi", "openai", "kimi-k3")

    assert console.export_text() == (
        "partial\nError from SynapseAPI: Agent run failed unexpectedly\n"
    )


def test_error_event_detail_is_not_interpreted_as_markup(monkeypatch, console):
    fake_post(monkeypatch, error_event("bad [bold]input[/bold]"))

    stream_response("key", "hi", "openai", "kimi-k3")

    assert "bad [bold]input[/bold]" in console.export_text()


def test_error_event_keeps_thread_id(monkeypatch, console):
    fake_post(monkeypatch, [*thread_event(THREAD_A), *error_event("failed")])

    assert stream_response("key", "hi", "openai", "kimi-k3") == THREAD_A


# --- parse_event and iter_events ---------------------------------------------


def test_parse_event_returns_event_dict():
    event = parse_event('{"type": "thread", "data": {"thread_id": "t"}}')

    assert event == {"type": "thread", "data": {"thread_id": "t"}}


@pytest.mark.parametrize(
    "payload",
    ["not json", "[DONE]", "[1, 2]", '{"type": "message"}', '{"data": "text"}'],
)
def test_parse_event_rejects_non_events(payload):
    assert parse_event(payload) is None


def test_iter_events_yields_events_in_order_and_skips_invalid():
    lines = [
        *thread_event(THREAD_A),
        "data: garbage",
        "",
        *sse_lines("Hi"),
        *error_event("oops"),
    ]

    assert [e["type"] for e in iter_events(lines)] == ["thread", "message", "error"]


# --- Other event types -------------------------------------------------------


def other_events() -> list[str]:
    """Events the API sends that the CLI doesn't display."""
    events = [
        {"type": "tool_call", "data": {"name": "search", "args": "{}", "id": "1"}},
        {"type": "tool_result", "data": {"content": "result", "name": "search"}},
        {"type": "update", "data": {"model": {"messages": []}}},
        {"type": "message", "data": {"content": ""}},
    ]
    lines: list[str] = []
    for event in events:
        lines += [f"data: {json.dumps(event)}", ""]
    return lines


def test_non_text_events_print_nothing(monkeypatch, console):
    fake_post(monkeypatch, [*other_events(), *sse_lines("Answer")])

    stream_response("key", "hi", "openai", "kimi-k3")

    assert console.export_text() == "Answer\n"


def test_spinner_keeps_running_during_non_text_events(monkeypatch, console, spinner):
    fake_post(monkeypatch, [*other_events(), *sse_lines("Answer")])

    stream_response("key", "hi", "openai", "kimi-k3")

    assert spinner.events.index("stop") < spinner.events.index("print:Answer")
    assert spinner.events[:2] == ["start", "stop"]


def test_tool_result_content_is_not_printed(monkeypatch, console):
    fake_post(monkeypatch, other_events())

    stream_response("key", "hi", "openai", "kimi-k3")

    assert "result" not in console.export_text()


# --- Byte lines --------------------------------------------------------------


def test_byte_lines_are_decoded_as_utf8(monkeypatch, console):
    post = fake_post(monkeypatch, [])
    post.return_value.iter_lines.return_value = iter(
        line.encode("utf-8") for line in sse_lines("café ☕")
    )

    stream_response("key", "hi", "openai", "kimi-k3")

    assert console.export_text() == "café ☕\n"


# --- Invalid thread detection ------------------------------------------------


def test_invalid_thread_error_without_thread_is_reported(monkeypatch, console):
    fake_post(
        monkeypatch, [], status_code=404, error_body={"detail": "Invalid thread_id"}
    )

    result = stream_response("key", "hi", "openai", "kimi-k3")

    output = console.export_text()
    assert result is None
    assert "Error streaming response: 404 Error" in output
    assert "could not be found" not in output


def test_is_invalid_thread_without_response():
    assert _is_invalid_thread(requests.HTTPError("no response")) is False


def test_is_invalid_thread_with_non_dict_json():
    response = MagicMock(status_code=404)
    response.json.return_value = ["Invalid thread_id"]

    assert _is_invalid_thread(requests.HTTPError(response=response)) is False


@pytest.mark.parametrize(
    ("status_code", "detail", "expected"),
    [
        (404, "Invalid thread_id", True),
        (404, "Not Found", False),
        (400, "Invalid thread_id", False),
    ],
)
def test_is_invalid_thread(status_code, detail, expected):
    response = MagicMock(status_code=status_code)
    response.json.return_value = {"detail": detail}

    assert _is_invalid_thread(requests.HTTPError(response=response)) is expected


# --- Terminal escape sequences -----------------------------------------------


def test_escape_sequences_in_content_are_stripped(monkeypatch, console):
    fake_post(monkeypatch, sse_lines("safe\x1b[2J", "\x1b]0;pwned\x07 text"))

    stream_response("key", "hi", "openai", "kimi-k3")

    output = console.export_text()
    assert "\x1b" not in output
    assert output == "safe[2J]0;pwned text\n"


def test_escape_sequences_in_error_detail_are_stripped(monkeypatch, console):
    fake_post(monkeypatch, error_event("bad\x1b[2J"))

    stream_response("key", "hi", "openai", "kimi-k3")

    assert "\x1b" not in console.export_text()
