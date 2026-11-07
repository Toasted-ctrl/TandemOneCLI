import io

import pytest
from rich.console import Console

from tandemonecli.stream.markdown import MarkdownPrinter, split_finished

# --- split_finished ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "finished", "pending"),
    [
        ("", "", ""),
        ("Hello", "", "Hello"),
        ("Hello\n", "", "Hello\n"),
        # A blank line alone doesn't end a block until the next one starts
        ("Hello\n\n", "", "Hello\n\n"),
        ("Hello\n\nW", "Hello\n\n", "W"),
        ("One\n\nTwo\n\nThr", "One\n\nTwo\n\n", "Thr"),
        ("One\n\n\n\nTwo", "One\n\n\n\n", "Two"),
        # Lines without a blank line between them belong together
        ("- a\n- b\n- c", "", "- a\n- b\n- c"),
        ("| a | b |\n|---|---|\n| 1 | 2 |", "", "| a | b |\n|---|---|\n| 1 | 2 |"),
        # An indented line may continue a list item
        ("- item\n\n  more of it\n", "", "- item\n\n  more of it\n"),
        ("- item\n\n  more\n\nNext", "- item\n\n  more\n\n", "Next"),
    ],
)
def test_split_finished(text, finished, pending):
    assert split_finished(text) == (finished, pending)


def test_blank_lines_in_code_blocks_dont_end_a_block():
    text = "```python\ndef a():\n    pass\n\n\ndef b():"

    assert split_finished(text) == ("", text)


def test_code_block_ends_at_its_closing_fence():
    text = "```\ncode\n\nmore\n```\n\nAfter"

    assert split_finished(text) == ("```\ncode\n\nmore\n```\n\n", "After")


@pytest.mark.parametrize(
    "text",
    [
        # A shorter fence or another fence character doesn't close the block
        "````\ncode\n```\n\nstill code",
        "~~~\ncode\n```\n\nstill code",
        # Text after the fence characters doesn't close it either
        "```\ncode\n``` x\n\nstill code",
        # An incomplete last line can't close the block yet
        "```\ncode\n\n``",
    ],
)
def test_code_block_stays_open(text):
    assert split_finished(text) == ("", text)


def test_tilde_fences_work_too():
    text = "~~~\ncode\n\n~~~\n\nAfter"

    assert split_finished(text) == ("~~~\ncode\n\n~~~\n\n", "After")


# --- MarkdownPrinter ---------------------------------------------------------


def recording_console(**kwargs) -> Console:
    return Console(record=True, width=60, force_terminal=False, **kwargs)


def output(console: Console) -> str:
    return "\n".join(line.rstrip() for line in console.export_text().split("\n"))


def print_reply(*chunks: str) -> str:
    console = recording_console()
    printer = MarkdownPrinter(console)
    for chunk in chunks:
        printer.add(chunk)
    printer.finish()
    return output(console)


def test_renders_markdown():
    assert print_reply("Some **bold**", " and `code`.") == "Some bold and code.\n"


def test_renders_lists():
    assert print_reply("Steps:\n\n- one\n- two") == "Steps:\n\n • one\n • two\n"


def test_renders_code_blocks_without_markdown_inside():
    reply = print_reply("```python\nx = '**not bold**'\n```")

    assert "x = '**not bold**'" in reply


def test_blocks_are_separated_by_one_blank_line():
    reply = print_reply("First.", "\n\nSecond.\n\n", "- item\n\nThird.")

    assert reply == "First.\n\nSecond.\n\n • item\n\nThird.\n"


def test_links_show_their_address():
    assert print_reply("See [the docs](https://example.com/docs).") == (
        "See the docs (https://example.com/docs).\n"
    )


def test_rich_markup_is_not_interpreted():
    assert print_reply("use [bold]this[/bold]") == "use [bold]this[/bold]\n"


def test_control_characters_are_removed():
    console = recording_console()
    printer = MarkdownPrinter(console)

    printer.add("safe\x1b[2J text\r\n\nnext\x07")
    printer.finish()

    text = console.export_text()
    assert "\x1b" not in text
    assert "\x07" not in text
    assert "\r" not in text


def test_finished_blocks_print_before_the_reply_ends():
    console = recording_console()
    printer = MarkdownPrinter(console)

    printer.add("First block.\n\nSecond")

    assert output(console) == "First block.\n"
    printer.finish()
    assert output(console) == "\nSecond\n"


def test_empty_reply_prints_nothing():
    assert print_reply() == ""
    assert print_reply("\n\n", "  ") == ""


def test_finish_twice_is_harmless():
    console = recording_console()
    printer = MarkdownPrinter(console)
    printer.add("Text")

    printer.finish()
    printer.finish()

    assert output(console) == "Text\n"


# --- In a terminal -----------------------------------------------------------


def terminal_console(height: int = 10) -> Console:
    return Console(
        file=io.StringIO(), force_terminal=True, width=40, height=height, record=True
    )


def test_block_being_written_is_shown_live_in_a_terminal():
    console = terminal_console()
    printer = MarkdownPrinter(console)

    printer.add("Still writing")

    assert printer._live is not None
    assert printer._live.is_started
    printer.finish()
    assert printer._live is None


def test_finished_blocks_are_printed_above_the_live_block():
    console = terminal_console()
    printer = MarkdownPrinter(console)

    printer.add("Done.\n\nWriting")
    printer.finish()

    raw = console.file.getvalue()  # type: ignore[attr-defined]
    # Each block is printed for good exactly once; the live view is cleared
    assert raw.count("Done.") == 1
    assert "Writing" in raw


def test_tall_live_block_shows_its_last_lines():
    console = terminal_console(height=6)
    printer = MarkdownPrinter(console)
    code = "\n".join(f"line {n}" for n in range(30))

    printer.add(f"```\n{code}")
    assert printer._live is not None
    rendered = console.render_lines(printer._live.renderable, pad=False)

    assert len(rendered) <= 4
    texts = ["".join(segment.text for segment in line).strip() for line in rendered]
    assert "line 29" in texts
    assert "line 0" not in texts
    printer.finish()
