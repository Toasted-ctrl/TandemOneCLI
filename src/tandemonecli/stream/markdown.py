"""Prints a streamed reply as Markdown.

Re-rendering the whole reply for every chunk flickers, and breaks once the reply
is taller than the terminal. So the reply is split into blocks (paragraphs, lists,
code blocks and so on). Finished blocks are printed once, for good. Only the block
that is still being written is shown live, and redrawn as it grows.
"""

import re

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.segment import Segment
from rich.text import Text

from tandemonecli.text import strip_control_chars

# The opening line of a fenced code block: ``` or ~~~, indented up to 3 spaces
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# A list item; Rich puts a blank line before lists itself
_LIST_ITEM = re.compile(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)")
# How often the block being written is redrawn
_REFRESHES_PER_SECOND = 10
# Lines of the terminal kept free below the block being written
_SPARE_LINES = 2


def split_finished(text: str) -> tuple[str, str]:
    """Split Markdown text into finished blocks and the block still being written.

    A block ends at a blank line, once the next line has started without
    indentation. A blank line inside a fenced code block doesn't count, and
    neither does one followed by an indented line, which may continue a list item.
    """
    lines = text.split("\n")
    boundary = 0
    offset = 0
    fence: str | None = None
    after_blank = False
    for index, line in enumerate(lines):
        if fence is None and after_blank and line and not line[0].isspace():
            boundary = offset
        # The last line may still be incomplete, so it can't open or close a fence
        if index == len(lines) - 1:
            break
        if fence is None:
            if match := _FENCE.match(line):
                fence = match.group(1)
            after_blank = not line.strip()
        else:
            if _closes_fence(line, fence):
                fence = None
            after_blank = False
        offset += len(line) + 1
    return text[:boundary], text[boundary:]


def _closes_fence(line: str, fence: str) -> bool:
    """Whether ``line`` closes a code block opened with ``fence``."""
    stripped = line.strip()
    return (
        len(line) - len(line.lstrip(" ")) <= 3
        and len(stripped) >= len(fence)
        and set(stripped) == {fence[0]}
    )


class _LastLines:
    """Shows only the last lines of a renderable that fit on the screen."""

    def __init__(self, renderable: RenderableType):
        self.renderable = renderable

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        lines = console.render_lines(self.renderable, options, pad=False)
        max_lines = max(console.size.height - _SPARE_LINES, 1)
        new_line = Segment.line()
        for line in lines[-max_lines:]:
            yield from line
            yield new_line


class MarkdownPrinter:
    """Prints a reply as Markdown while it streams in.

    Call ``add`` for each chunk of text and ``finish`` at the end, also when the
    stream fails, so the rest is printed and the terminal is left in order.
    Terminal control characters are removed, and links show their address, so the
    reply can't control the terminal or hide where a link goes.
    """

    def __init__(self, console: Console):
        self.console = console
        self._pending = ""
        self._printed = False
        self._live: Live | None = None

    def add(self, chunk: str) -> None:
        self._pending += strip_control_chars(chunk)
        finished, self._pending = split_finished(self._pending)
        if finished.strip():
            self._print(finished)
        self._show_pending()

    def finish(self) -> None:
        """Print what's left of the reply."""
        if self._live is not None:
            self._live.stop()
            self._live = None
        if self._pending.strip():
            self._print(self._pending)
        self._pending = ""

    def pause(self) -> None:
        """Print what's pending, so other output can be shown before more chunks.

        The reply simply continues when ``add`` is called again; what was printed
        stays on screen with a blank line between it and what follows.
        """
        self.finish()

    def _render(self, text: str) -> RenderableType:
        markdown = Markdown(text.strip("\n"), hyperlinks=False)
        # Leave a blank line between blocks, as Markdown does, unless Rich adds one
        if self._printed and not _LIST_ITEM.match(text.lstrip("\n")):
            return Group(Text(), markdown)
        return markdown

    def _print(self, text: str) -> None:
        # Printing while the live block is shown puts the text above it
        self.console.print(self._render(text))
        self._printed = True

    def _show_pending(self) -> None:
        # Without a terminal (output piped to a file, say) there's nothing to
        # redraw, so the rest is printed by finish
        if not self.console.is_terminal or not self._pending.strip():
            return
        if self._live is None:
            self._live = Live(
                console=self.console,
                refresh_per_second=_REFRESHES_PER_SECOND,
                transient=True,
            )
            self._live.start()
        self._live.update(_LastLines(self._render(self._pending)))
