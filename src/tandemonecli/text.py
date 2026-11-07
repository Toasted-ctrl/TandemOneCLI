"""Helpers for safely printing text that comes from the server or a model."""

import re

from rich.markup import escape

# C0 control characters (except tab and newline), DEL, and C1 control characters.
# These include ESC (\x1b) and CSI (\x9b), which start terminal escape sequences
# that can clear the screen, change the window title or move the cursor.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_control_chars(text: str) -> str:
    """Remove characters that the terminal would treat as commands."""
    return _CONTROL_CHARS.sub("", text)


def safe_markup(text: str) -> str:
    """Make untrusted text safe to embed in a Rich markup string.

    Removes terminal control characters and escapes Rich markup like ``[bold]``.
    """
    return escape(strip_control_chars(text))


def show_control_chars(text: str) -> str:
    """Replace control characters with visible ``\\xNN`` escapes.

    Use this where the user must see exactly what the text contains, such as a
    command they are asked to approve, instead of silently dropping them.
    """
    return _CONTROL_CHARS.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
