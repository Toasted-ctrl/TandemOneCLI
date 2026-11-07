import pytest

from tandemonecli.text import safe_markup, show_control_chars, strip_control_chars


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A\x1b[2JB", "A[2JB"),  # clear screen
        ("A\x1b]0;title\x07B", "A]0;titleB"),  # set window title
        ("A\x9b2JB", "A2JB"),  # 8-bit CSI
        ("A\rB", "AB"),  # carriage return could overwrite the line
        ("A\x00\x08\x7fB", "AB"),
    ],
)
def test_strip_control_chars_removes_terminal_commands(text, expected):
    assert strip_control_chars(text) == expected


def test_strip_control_chars_keeps_normal_text():
    text = "Line one\n\tindented — café ☕ [bold]"

    assert strip_control_chars(text) == text


def test_safe_markup_escapes_markup_and_strips_control_chars():
    # "[31m" isn't a valid Rich tag, so Rich leaves it as plain text
    assert safe_markup("\x1b[31m[bold]hi[/bold]") == "[31m\\[bold]hi\\[/bold]"


def test_show_control_chars_makes_them_visible():
    assert show_control_chars("rm\x1b[2K -rf\r\n\tok") == "rm\\x1b[2K -rf\\x0d\n\tok"
