"""Which commands of the model run without asking the user first.

Two kinds of commands are allowed:

- Built-in read-only commands such as ``ls``, ``cat`` and ``git status``, unless they
  use an option that writes files or runs other programs (``find -exec``, say).
  They run in the sandbox, so they can only read the project and system files.
- The user's own rules, saved in ``~/.config/tandemone/permissions.json``. A rule is
  the start of a command: ``uv run pytest`` also allows ``uv run pytest -q``.

A command is allowed only if every part of it is (``ls | grep x`` checks ``ls`` and
``grep x``), and ``tandemonecli.tools.shell`` can read it. Anything else needs the
user's approval.

The sandbox hides the permissions file from commands, so the model can't change it.
"""

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tandemonecli.config.paths import user_config_dir
from tandemonecli.tools.shell import SimpleCommand, split_command

PERMISSIONS_FILE = user_config_dir() / "permissions.json"


@dataclass(frozen=True)
class ReadOnlyCommand:
    """A built-in command that only reads, and the options that would change that."""

    prefix: tuple[str, ...]
    # Options that write files or run programs. Long options also count when
    # abbreviated, since many programs accept that (--out for --output).
    forbidden: tuple[str, ...] = ()
    # Short options that are forbidden, also when combined (-ro includes -o)
    forbidden_short: str = ""

    def matches(self, command: SimpleCommand) -> bool:
        words = command.words
        if words[: len(self.prefix)] != self.prefix:
            return False
        if not self.forbidden and not self.forbidden_short:
            return True
        # An expanded glob could turn into a forbidden option
        if command.expands:
            return False
        return not any(self._is_forbidden(word) for word in words[len(self.prefix) :])

    def _is_forbidden(self, word: str) -> bool:
        name = word.split("=", 1)[0]
        for option in self.forbidden:
            if name == option:
                return True
            if name.startswith("--") and len(name) > 2 and option.startswith(name):
                return True
        if self.forbidden_short and word.startswith("-") and not word.startswith("--"):
            return any(letter in word[1:] for letter in self.forbidden_short)
        return False


def _simple(*names: str) -> list[ReadOnlyCommand]:
    return [ReadOnlyCommand((name,)) for name in names]


_GIT_READ_ONLY = ("status", "log", "diff", "show", "blame", "ls-files", "rev-parse")

READ_ONLY_COMMANDS: tuple[ReadOnlyCommand, ...] = (
    *_simple("ls", "cat", "head", "tail", "wc", "pwd", "cd", "echo", "grep"),
    *_simple("egrep", "fgrep", "du", "stat", "diff", "cut", "tr", "basename"),
    *_simple("dirname", "realpath", "which"),
    ReadOnlyCommand(("rg",), forbidden=("--pre", "--hostname-bin")),
    ReadOnlyCommand(
        ("find",),
        forbidden=(
            "-exec",
            "-execdir",
            "-ok",
            "-okdir",
            "-delete",
            "-fls",
            "-fprint",
            "-fprint0",
            "-fprintf",
        ),
    ),  # fmt: skip
    ReadOnlyCommand(("tree",), forbidden=("--output",), forbidden_short="oR"),
    ReadOnlyCommand(("file",), forbidden=("--compile",), forbidden_short="C"),
    ReadOnlyCommand(
        ("sort",), forbidden=("--output", "--compress-program"), forbidden_short="o"
    ),
    *(
        ReadOnlyCommand(("git", subcommand), forbidden=("--output",))
        for subcommand in _GIT_READ_ONLY
    ),
)


class ProjectPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_commands: list[str] = Field(default_factory=list)


class Permissions(BaseModel):
    """The contents of permissions.json."""

    model_config = ConfigDict(extra="forbid")

    # Whether the built-in read-only commands run without asking
    allow_read_only_commands: bool = True
    # Rules for every project
    allowed_commands: list[str] = Field(default_factory=list)
    # Rules per project, by the project's absolute path
    projects: dict[str, ProjectPermissions] = Field(default_factory=dict)

    def rules_for(self, project: Path) -> list[tuple[str, ...]]:
        """Return the user's rules that apply in ``project``, split into words."""
        texts = list(self.allowed_commands)
        if entry := self.projects.get(str(project)):
            texts += entry.allowed_commands
        rules = []
        for text in texts:
            try:
                words = tuple(shlex.split(text))
            except ValueError:
                # An unclosed quote; skip the rule rather than guess
                continue
            if words:
                rules.append(words)
        return rules


class PermissionsError(Exception):
    """permissions.json can't be read or saved."""


def load_permissions(path: Path | None = None) -> Permissions:
    """Read the permissions file; without one, only the defaults apply."""
    path = path or PERMISSIONS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Permissions()
    except OSError as e:
        raise PermissionsError(f"can't read {path}: {e.strerror}") from None
    try:
        return Permissions.model_validate_json(text)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'file'}: {error['msg']}"
            for error in e.errors()
        )
        raise PermissionsError(f"{path} isn't valid: {problems}") from None


def save_permissions(permissions: Permissions, path: Path | None = None) -> None:
    """Write the permissions file, replacing it in one step so it's never half written."""
    path = path or PERMISSIONS_FILE
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(permissions.model_dump_json(indent=2) + "\n", "utf-8")
        os.replace(temporary, path)
    except OSError as e:
        raise PermissionsError(f"can't save {path}: {e.strerror}") from None


def is_allowed(command: str, permissions: Permissions, project: Path) -> bool:
    """Whether ``command`` may run in ``project`` without asking the user."""
    commands = split_command(command)
    if not commands:
        return False
    rules = permissions.rules_for(project)
    read_only = READ_ONLY_COMMANDS if permissions.allow_read_only_commands else ()
    return all(
        any(rule.matches(part) for rule in read_only)
        or any(part.words[: len(rule)] == rule for rule in rules)
        for part in commands
    )


# A word of a suggested rule should be a command or subcommand name, not an
# option, path, file name, assignment or pattern
_NOT_A_NAME = frozenset("-/.=~*?[{")
# Hyphens are fine inside a name, as in "git ls-files"
_NOT_A_NAME_INSIDE = _NOT_A_NAME - {"-"}
_MAX_RULE_WORDS = 3


def suggest_rule(command: str) -> str | None:
    """Suggest a rule that allows ``command`` and similar ones, if there is one.

    The rule is the command's leading names, before the first option or path:
    ``uv run pytest -q tests`` gives ``uv run pytest``. Commands made of several
    parts, or that can't be read, get no suggestion. Neither do built-in commands
    with forbidden options (``find``, say): their safe uses are allowed already, so
    a rule would only add the forbidden ones.
    """
    commands = split_command(command)
    if commands is None or len(commands) != 1:
        return None
    prefix: list[str] = []
    for word in commands[0].words[:_MAX_RULE_WORDS]:
        if (
            not word
            or word[0] in _NOT_A_NAME
            or any(c in _NOT_A_NAME_INSIDE for c in word)
        ):
            break
        prefix.append(word)
    if not prefix:
        return None
    for read_only in READ_ONLY_COMMANDS:
        restricted = read_only.forbidden or read_only.forbidden_short
        if restricted and tuple(prefix[: len(read_only.prefix)]) == read_only.prefix:
            return None
    return shlex.join(prefix)


def allow_in_project(rule: str, project: Path, path: Path | None = None) -> None:
    """Save ``rule`` for ``project`` in the permissions file."""
    permissions = load_permissions(path)
    entry = permissions.projects.setdefault(str(project), ProjectPermissions())
    if rule not in entry.allowed_commands:
        entry.allowed_commands.append(rule)
    save_permissions(permissions, path)
