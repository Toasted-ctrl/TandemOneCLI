"""Splits a shell command into simple commands, to check them against rules.

Only a small, safe part of bash is understood: words, quotes, backslashes, and the
separators ``&&``, ``||``, ``|`` and ``;``. Anything else (``$``, backticks,
redirects, subshells, background jobs, line breaks) makes ``split_command`` return
None, so the command needs the user's approval. Being strict here only costs an
extra question, while a command that bash reads differently from this module could
slip past the rules.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SimpleCommand:
    """One command of a pipeline or list, such as ``grep foo`` in ``ls | grep foo``."""

    words: tuple[str, ...]
    # Whether a word has an unquoted glob or brace, which bash may expand into
    # other words, even options (from a file named "-delete", for example)
    expands: bool = False


# Variables, command substitution and line breaks could hide anything
_REJECTED_CHARS = frozenset("$`\n\r")
# Redirects, subshells and background jobs
_REJECTED_OPERATORS = frozenset("<>()&")
_EXPANDING_CHARS = frozenset("*?[{")
_WHITESPACE = frozenset(" \t")
# Common redirects that only discard output or merge stderr into stdout
_HARMLESS_REDIRECT = re.compile(r"(?<!\S)(?:[12]?>/dev/null|2>&1)(?!\S)")


def split_command(command: str) -> list[SimpleCommand] | None:
    """Split ``command`` into simple commands, or return None if it isn't simple.

    Each word is returned the way bash passes it to the program: quotes are removed
    and backslash escapes resolved.
    """
    if any(char in _REJECTED_CHARS for char in command):
        return None
    command = _HARMLESS_REDIRECT.sub(" ", command)

    commands: list[SimpleCommand] = []
    words: list[str] = []
    word: list[str] = []
    in_word = False
    expands = False

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            words.append("".join(word))
            word.clear()
            in_word = False

    def end_command() -> bool:
        """Finish the current command; False if it's empty (as in ``ls ;; pwd``)."""
        nonlocal expands
        end_word()
        if not words:
            return False
        commands.append(SimpleCommand(tuple(words), expands))
        words.clear()
        expands = False
        return True

    i = 0
    while i < len(command):
        char = command[i]
        if char in _WHITESPACE:
            end_word()
            i += 1
        elif char == "'":
            # Everything up to the next single quote is literal
            end = command.find("'", i + 1)
            if end == -1:
                return None
            word.append(command[i + 1 : end])
            in_word = True
            i = end + 1
        elif char == '"':
            # $ and ` are rejected above, so only backslashes are special here
            i += 1
            while True:
                if i >= len(command):
                    return None
                char = command[i]
                if char == '"':
                    break
                if char == "\\" and command[i + 1 : i + 2] in ('"', "\\"):
                    word.append(command[i + 1])
                    i += 2
                else:
                    word.append(char)
                    i += 1
            in_word = True
            i += 1
        elif char == "\\":
            if i + 1 >= len(command):
                return None
            word.append(command[i + 1])
            in_word = True
            i += 2
        elif command[i : i + 2] in ("&&", "||"):
            if not end_command():
                return None
            i += 2
        elif char in "|;":
            if not end_command():
                return None
            i += 1
        elif char in _REJECTED_OPERATORS:
            return None
        else:
            if char in _EXPANDING_CHARS:
                expands = True
            word.append(char)
            in_word = True
            i += 1

    if not end_command():
        return None
    return commands
