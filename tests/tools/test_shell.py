import pytest

from tandemonecli.tools.shell import SimpleCommand, split_command


def words(command: str) -> list[tuple[str, ...]] | None:
    parts = split_command(command)
    return None if parts is None else [part.words for part in parts]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls", [("ls",)]),
        ("ls -la src", [("ls", "-la", "src")]),
        ("  ls\t-la  ", [("ls", "-la")]),
        ("ls | grep x", [("ls",), ("grep", "x")]),
        (
            "ls && pwd || echo no; cat a",
            [("ls",), ("pwd",), ("echo", "no"), ("cat", "a")],
        ),
        ("ls|grep x", [("ls",), ("grep", "x")]),
    ],
)
def test_splits_simple_commands(command, expected):
    assert words(command) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("grep 'a b' file", ("grep", "a b", "file")),
        ('grep "a b" file', ("grep", "a b", "file")),
        (r"grep a\ b file", ("grep", "a b", "file")),
        (r'echo "say \"hi\" \\ \n"', ("echo", 'say "hi" \\ \\n')),
        ("echo ''", ("echo", "")),
        # Quotes inside a word are joined, as bash does
        ("find . '-de'lete", ("find", ".", "-delete")),
        ('find . -"exec"', ("find", ".", "-exec")),
        (r"find . -\delete", ("find", ".", "-delete")),
    ],
)
def test_removes_quotes_like_bash(command, expected):
    assert words(command) == [expected]


@pytest.mark.parametrize(
    "command",
    [
        "grep ';' file",
        "grep '|' file",
        'grep "&&" file',
        r"grep \| file",
    ],
)
def test_quoted_operators_are_words(command):
    parts = words(command)

    assert parts is not None
    assert len(parts) == 1


@pytest.mark.parametrize(
    "command",
    [
        # Variables and command substitution
        "echo $HOME",
        "echo ${x}",
        "ls $(rm -rf .)",
        "ls `rm -rf .`",
        "echo '$HOME'",
        # Redirects
        "cat a > b",
        "cat a >> b",
        "cat < a",
        "cat <<< text",
        "diff <(ls) b",
        "ls &> out",
        # Subshells, background jobs and line breaks
        "(rm -rf .)",
        "ls & rm -rf .",
        "ls |& grep x",
        "ls\nrm -rf .",
        "ls\rrm -rf .",
        # Empty parts and unclosed quotes
        "",
        "   ",
        "ls ;; pwd",
        "| ls",
        "ls &&",
        "echo 'unclosed",
        'echo "unclosed',
        "echo trailing\\",
    ],
)
def test_rejects_what_it_cant_check(command):
    assert split_command(command) is None


@pytest.mark.parametrize(
    "command",
    ["ls 2>&1", "ls 2>/dev/null", "ls >/dev/null", "ls 1>/dev/null | grep x 2>&1"],
)
def test_allows_redirects_that_only_discard_output(command):
    parts = split_command(command)

    assert parts is not None
    assert all(">" not in word for part in parts for word in part.words)


def test_redirect_to_a_file_named_like_dev_null_is_rejected():
    assert split_command("ls >/dev/nullx") is None


@pytest.mark.parametrize("command", ["ls *.py", "cat file?", "ls [ab]", "ls {a,b}"])
def test_marks_words_bash_expands(command):
    assert split_command(command) == [
        SimpleCommand(tuple(command.split()), expands=True)
    ]


@pytest.mark.parametrize("command", ["ls '*.py'", 'ls "{a,b}"', r"ls \*"])
def test_quoted_glob_does_not_expand(command):
    parts = split_command(command)

    assert parts is not None
    assert parts[0].expands is False


def test_expansion_is_tracked_per_command():
    parts = split_command("ls *.py | grep x")

    assert parts is not None
    assert [part.expands for part in parts] == [True, False]
