"""Keeps lines, such as the empty input line, at the bottom of the terminal.

While a reply streams in, the terminal's scrolling region is limited to the lines
above the footer: the reply scrolls there, and the footer stays put. Lines that
scroll off the top still go to the terminal's scrollback.

Only on terminals; elsewhere (output piped to a file, say) nothing is pinned.
"""

import io

from rich.console import Console, RenderableType

# Lines the reply keeps at least; in a smaller terminal the footer isn't pinned
MIN_REPLY_LINES = 5

SAVE_CURSOR = "\x1b7"
RESTORE_CURSOR = "\x1b8"
RESET_SCROLL_REGION = "\x1b[r"
CLEAR_LINE = "\x1b[2K"
CLEAR_TO_END_OF_SCREEN = "\x1b[J"


def move_to(row: int) -> str:
    """Move the cursor to the start of ``row``, counted from 1."""
    return f"\x1b[{row};1H"


class PinnedFooter:
    """Pins ``footer`` to the bottom of the terminal, from ``pin`` to ``unpin``.

    ``unpin`` leaves the cursor where the footer was, so whatever is printed
    next, such as the input line itself, takes its place.
    """

    def __init__(self, console: Console, footer: RenderableType | None) -> None:
        self.console = console
        self.footer = footer
        # The terminal size the footer is pinned for; None while it isn't
        self._size: tuple[int, int] | None = None
        self._lines = 0

    @property
    def lines(self) -> int:
        """How many lines at the bottom the footer takes; 0 while it isn't pinned."""
        return self._lines if self._size is not None else 0

    def pin(self) -> None:
        """Draw the footer at the bottom, and keep everything else above it."""
        if self._size is not None or self.footer is None:
            return
        if not self.console.is_terminal or self.console.is_dumb_terminal:
            return
        self._apply()

    def refresh(self) -> None:
        """Pin the footer again if the terminal was resized."""
        if self._size is not None and tuple(self.console.size) != self._size:
            self._apply()

    def unpin(self) -> None:
        """Let the whole screen scroll again, and clear the footer."""
        if self._size is None:
            return
        height = self.console.size.height
        top = max(height - self._lines + 1, 1)
        self._write(
            # Resetting the region moves the cursor to the top, so keep it in place
            SAVE_CURSOR
            + RESET_SCROLL_REGION
            + RESTORE_CURSOR
            + move_to(top)
            + CLEAR_TO_END_OF_SCREEN
        )
        self._size = None

    def _apply(self) -> None:
        width, height = self.console.size
        lines = self._render(width)
        count = len(lines)
        if height < count + MIN_REPLY_LINES:
            # Too small: rather no footer than no room for the reply
            if self._size is not None:
                self.unpin()
            return
        self._write(
            # If the cursor is where the footer goes, scroll the screen up first
            "\n" * count
            + f"\x1b[{count}A"
            # Setting the region moves the cursor to the top, so keep it in place
            + SAVE_CURSOR
            + f"\x1b[1;{height - count}r"
            + RESTORE_CURSOR
            # Draw each line at its own place, outside the region
            + SAVE_CURSOR
            + "".join(
                move_to(height - count + 1 + index) + CLEAR_LINE + line
                for index, line in enumerate(lines)
            )
            + RESTORE_CURSOR
        )
        self._size = (width, height)
        self._lines = count

    def _render(self, width: int) -> list[str]:
        """The footer as lines of text with the console's colours."""
        buffer = io.StringIO()
        Console(
            file=buffer,
            width=width,
            force_terminal=True,
            color_system=self.console.color_system,  # type: ignore[arg-type]
            highlight=False,
        ).print(self.footer)
        return buffer.getvalue().splitlines()

    def _write(self, text: str) -> None:
        # Under Rich's own lock, so a live view refreshing in its own thread
        # can't write in between
        with self.console._lock:
            self.console.file.write(text)
            self.console.file.flush()
