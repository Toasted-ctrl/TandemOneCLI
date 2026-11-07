import json
from pathlib import Path

import pytest

from tandemonecli.permissions import (
    Permissions,
    PermissionsError,
    ProjectPermissions,
    allow_in_project,
    is_allowed,
    load_permissions,
    save_permissions,
    suggest_rule,
)

PROJECT = Path("/home/user/project")


def allowed(command: str, permissions: Permissions | None = None) -> bool:
    return is_allowed(command, permissions or Permissions(), PROJECT)


# --- Built-in read-only commands ---------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "ls -la src",
        "cat README.md",
        "head -n 20 src/app.py",
        "grep -rn 'def main' src",
        "rg -n TODO",
        "find . -name '*.py'",
        "find . -type f -newer setup.py",
        "tree -L 2",
        "wc -l *.py",
        "cd src && ls",
        "git status",
        "git log --oneline -n 5",
        "git diff HEAD~1 -- src",
        "git show HEAD:README.md",
        "sort -k2 data.txt",
        "ls | grep x | wc -l",
        "grep -r foo . 2>/dev/null | head",
    ],
)
def test_read_only_commands_are_allowed(command):
    assert allowed(command)


@pytest.mark.parametrize(
    "command",
    [
        # Options that write files or run programs
        "find . -delete",
        "find . -name x -exec rm {} ;",
        "find . -execdir sh -c x",
        "find . -fprint out.txt",
        "rg --pre ./script x",
        "rg --pre=./script x",
        "rg --hostname-bin=./x foo",
        "sort -o out.txt data",
        "sort -ro out.txt data",
        "sort --output=out.txt data",
        "sort --out=out.txt data",
        "sort --compress-program=./x data",
        "tree -o out.txt",
        "tree -R -H . -L 1",
        "file -C -m magic",
        "git diff --output=patch.txt",
        "git log --output patch.txt",
        # A glob could expand into a forbidden option
        "find *",
        "find . {-delete,}",
        # Other git subcommands, and options before the subcommand
        "git commit -m x",
        "git push",
        "git -c core.pager=x log",
        "git -C .. status",
        # Not read-only
        "rm -rf .",
        "sed -i s/a/b/ file",
        "awk '{system(\"rm x\")}' file",
        "env rm x",
        "xargs rm",
        "tee out.txt",
        "python3 -c 'print(1)'",
        # One part that isn't allowed is enough
        "ls && rm -rf .",
        "ls; rm -rf .",
        "cat x | sh",
        "ls || curl evil.example",
        # Can't be checked
        "ls $(rm -rf .)",
        "cat a > b",
        "ls & rm -rf .",
        "FOO=1 ls",
    ],
)
def test_other_commands_need_approval(command):
    assert not allowed(command)


def test_read_only_commands_can_be_switched_off():
    assert not allowed("ls", Permissions(allow_read_only_commands=False))


# --- User rules --------------------------------------------------------------


def test_project_rule_allows_commands_starting_with_it():
    permissions = Permissions(
        projects={
            str(PROJECT): ProjectPermissions(allowed_commands=["uv run pytest"]),
        }
    )

    assert allowed("uv run pytest", permissions)
    assert allowed("uv run pytest -q tests/test_x.py", permissions)
    assert allowed("uv run pytest tests/*.py | tail -3", permissions)
    assert not allowed("uv run pytestx", permissions)
    assert not allowed("uv run ruff check", permissions)
    assert not allowed("uv run pytest; rm -rf .", permissions)


def test_project_rule_applies_only_in_its_project():
    permissions = Permissions(
        projects={"/elsewhere": ProjectPermissions(allowed_commands=["make"])}
    )

    assert not allowed("make", permissions)


def test_global_rule_applies_everywhere():
    assert allowed("make test", Permissions(allowed_commands=["make"]))


def test_rules_are_matched_word_by_word():
    permissions = Permissions(allowed_commands=["npm 'run test'", "npm  test"])

    assert allowed("npm test", permissions)
    assert allowed("npm 'run test' --watch", permissions)
    assert not allowed("npm run test", permissions)


def test_broken_rules_are_ignored():
    permissions = Permissions(allowed_commands=["", "make 'unclosed"])

    assert not allowed("make", permissions)
    assert not allowed("whoami", permissions)


# --- suggest_rule ------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "rule"),
    [
        ("uv run pytest -q tests", "uv run pytest"),
        ("uv run pytest", "uv run pytest"),
        ("npm test", "npm test"),
        ("npm run-script build -w", "npm run-script build"),
        # Up to three names; the user can edit the file for a broader rule
        ("cargo test my_crate", "cargo test my_crate"),
        ("make", "make"),
        ("cargo build --release", "cargo build"),
        ("python3 manage.py test", "python3"),
        ("docker compose up -d web", "docker compose up"),
        ("pip install -e .", "pip install"),
        # Built-ins without forbidden options, and other git subcommands
        ("ls -la", "ls"),
        ("git push origin main", "git push origin"),
    ],
)
def test_suggests_leading_names(command, rule):
    assert suggest_rule(command) == rule


@pytest.mark.parametrize(
    "command",
    [
        "./run-tests.sh",
        "-x",
        "ls && rm -rf .",
        "ls | grep x",
        "echo $HOME",
        "~/bin/tool",
        "*.sh",
        # A rule would allow the options that the built-in rules forbid
        "find . -name x -delete",
        "sort -o out data",
        "rg --pre ./x foo",
        "git diff --output=x",
        "tree -o out",
    ],
)
def test_no_suggestion_for_commands_that_cant_be_a_rule(command):
    assert suggest_rule(command) is None


def test_suggested_rule_quotes_words_with_spaces():
    assert suggest_rule("'my tool' run") == "'my tool' run"


# --- The permissions file ----------------------------------------------------


def test_missing_file_gives_defaults(tmp_path):
    assert load_permissions(tmp_path / "missing.json") == Permissions()


def test_saved_permissions_can_be_loaded(tmp_path):
    path = tmp_path / "dir" / "permissions.json"
    permissions = Permissions(
        allow_read_only_commands=False,
        allowed_commands=["make"],
        projects={"/p": ProjectPermissions(allowed_commands=["npm test"])},
    )

    save_permissions(permissions, path)

    assert load_permissions(path) == permissions
    assert not path.with_name("permissions.json.tmp").exists()


def test_file_is_readable_json(tmp_path):
    path = tmp_path / "permissions.json"

    allow_in_project("uv run pytest", PROJECT, path)

    assert json.loads(path.read_text()) == {
        "allow_read_only_commands": True,
        "allowed_commands": [],
        "projects": {str(PROJECT): {"allowed_commands": ["uv run pytest"]}},
    }


def test_partial_file_uses_defaults_for_the_rest(tmp_path):
    path = tmp_path / "permissions.json"
    path.write_text('{"allowed_commands": ["make"]}')

    permissions = load_permissions(path)

    assert permissions.allow_read_only_commands is True
    assert permissions.allowed_commands == ["make"]


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        '{"allowed_commands": "make"}',
        '{"allowed_command": ["make"]}',
        "[]",
    ],
)
def test_invalid_file_raises_readable_error(tmp_path, text):
    path = tmp_path / "permissions.json"
    path.write_text(text)

    with pytest.raises(PermissionsError, match="isn't valid"):
        load_permissions(path)


def test_allow_in_project_keeps_other_rules(tmp_path):
    path = tmp_path / "permissions.json"
    save_permissions(
        Permissions(
            allowed_commands=["make"],
            projects={"/other": ProjectPermissions(allowed_commands=["npm test"])},
        ),
        path,
    )

    allow_in_project("uv run pytest", PROJECT, path)
    allow_in_project("uv run pytest", PROJECT, path)

    permissions = load_permissions(path)
    assert permissions.allowed_commands == ["make"]
    assert permissions.projects["/other"].allowed_commands == ["npm test"]
    assert permissions.projects[str(PROJECT)].allowed_commands == ["uv run pytest"]


def test_allow_in_project_refuses_to_overwrite_an_invalid_file(tmp_path):
    path = tmp_path / "permissions.json"
    path.write_text("{broken")

    with pytest.raises(PermissionsError):
        allow_in_project("make", PROJECT, path)

    assert path.read_text() == "{broken"


def test_save_reports_unwritable_location(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")

    with pytest.raises(PermissionsError, match="can't save"):
        save_permissions(Permissions(), blocker / "permissions.json")
