import io

import pytest
from rich.console import Console, Group
from rich.text import Text

from tandemonecli.stream.footer import PinnedFooter

FOOTER = Group(Text("-----"), Text(">"), Text("-----"), Text("openai / gpt-a"))


def terminal_console(height: int = 12, **kwargs) -> Console:
    options = {"force_terminal": True, "color_system": None, **kwargs}
    return Console(file=io.StringIO(), width=20, height=height, **options)


def written(console: Console) -> str:
    assert isinstance(console.file, io.StringIO)
    return console.file.getvalue()


def test_pin_limits_scrolling_to_the_lines_above_the_footer():
    console = terminal_console(height=12)
    pinned = PinnedFooter(console, FOOTER)

    pinned.pin()

    assert pinned.lines == 4
    output = written(console)
    # Room for the footer, then a region of the top 8 lines, cursor kept in place
    assert output.startswith("\n\n\n\n\x1b[4A")
    assert "\x1b7\x1b[1;8r\x1b8" in output


def test_pin_draws_each_footer_line_on_the_bottom_lines():
    console = terminal_console(height=12)

    PinnedFooter(console, FOOTER).pin()

    output = written(console)
    for row, text in zip(range(9, 13), ["-----", ">", "-----", "openai / gpt-a"]):
        assert f"\x1b[{row};1H\x1b[2K{text}" in output
    # The cursor goes back to where the reply continues
    assert output.endswith("\x1b8")


def test_unpin_lets_the_whole_screen_scroll_and_clears_the_footer():
    console = terminal_console(height=12)
    pinned = PinnedFooter(console, FOOTER)
    pinned.pin()
    console.file = io.StringIO()

    pinned.unpin()

    # The cursor ends where the footer started, for the input line to take over
    assert written(console) == "\x1b7\x1b[r\x1b8\x1b[9;1H\x1b[J"
    assert pinned.lines == 0


def test_unpin_without_pin_writes_nothing():
    console = terminal_console()

    PinnedFooter(console, FOOTER).unpin()

    assert written(console) == ""


@pytest.mark.parametrize(
    "console",
    [
        Console(file=io.StringIO(), force_terminal=False, width=20, height=12),
        Console(file=io.StringIO(), force_terminal=True, width=20, height=8),
    ],
    ids=["not a terminal", "too small"],
)
def test_footer_is_not_pinned_where_it_cant_be(console):
    pinned = PinnedFooter(console, FOOTER)

    pinned.pin()
    pinned.unpin()

    assert written(console) == ""
    assert pinned.lines == 0


def test_no_footer_pins_nothing():
    console = terminal_console()
    pinned = PinnedFooter(console, None)

    pinned.pin()

    assert written(console) == ""
    assert pinned.lines == 0


def test_refresh_pins_again_after_a_resize():
    console = terminal_console(height=12)
    pinned = PinnedFooter(console, FOOTER)
    pinned.pin()
    console.file = io.StringIO()

    pinned.refresh()
    assert written(console) == ""

    console.size = (20, 16)
    pinned.refresh()
    assert "\x1b[1;12r" in written(console)
    assert "\x1b[13;1H" in written(console)


def test_refresh_unpins_when_the_terminal_gets_too_small():
    console = terminal_console(height=12)
    pinned = PinnedFooter(console, FOOTER)
    pinned.pin()

    console.size = (20, 6)
    pinned.refresh()

    assert pinned.lines == 0
    assert written(console).endswith("\x1b[r\x1b8\x1b[3;1H\x1b[J")


def test_footer_keeps_its_colours():
    console = terminal_console(color_system="standard")

    PinnedFooter(console, Text("web", style="green")).pin()

    assert "\x1b[32mweb\x1b[0m" in written(console)
