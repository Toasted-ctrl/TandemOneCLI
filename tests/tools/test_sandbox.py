import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tandemonecli.tools import sandbox
from tandemonecli.tools.bash import run_bash
from tandemonecli.tools.sandbox import (
    UvSetup,
    find_uv,
    sandbox_args,
    sandbox_command,
    sandbox_env,
    sandbox_problem,
)


@pytest.fixture(autouse=True)
def clear_caches():
    """Each test checks the sandbox afresh."""
    # Kept, since tests may replace them with fakes that have no cache
    cached = (sandbox._probe, sandbox.supports_overlay)
    for function in cached:
        function.cache_clear()
    yield
    for function in cached:
        function.cache_clear()


@pytest.fixture
def no_uv(monkeypatch):
    monkeypatch.setattr(sandbox, "find_uv", lambda: None)


@pytest.fixture
def fake_uv(monkeypatch, tmp_path) -> UvSetup:
    """A uv install in a temporary home directory, as with the standalone installer."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("uv", "uvx"):
        (bin_dir / name).touch(mode=0o755)
    cache_dir = tmp_path / "cache" / "uv"
    cache_dir.mkdir(parents=True)
    python_dir = tmp_path / "data" / "uv" / "python"
    python_dir.mkdir(parents=True)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    return UvSetup(
        mounts=tuple((bin_dir / name, bin_dir / name) for name in ("uv", "uvx")),
        bin_dir=bin_dir,
        cache_dir=cache_dir,
        python_dir=python_dir,
    )


def option_values(args: list[str], option: str) -> list[list[str]]:
    """Return the arguments given to each use of a bwrap option with two values."""
    return [args[i + 1 : i + 3] for i, arg in enumerate(args) if arg == option]


# --- sandbox_args / sandbox_command ------------------------------------------


@pytest.mark.usefixtures("no_uv")
def test_root_is_the_only_writable_bind(tmp_path):
    args = sandbox_args(tmp_path)

    assert option_values(args, "--bind") == [[str(tmp_path), str(tmp_path)]]
    assert args[args.index("--chdir") + 1] == str(tmp_path)


def test_system_dirs_are_read_only(tmp_path):
    read_only = [
        src for src, _ in option_values(sandbox_args(tmp_path), "--ro-bind-try")
    ]

    assert {"/usr", "/etc"} <= set(read_only)


@pytest.mark.usefixtures("no_uv")
def test_home_and_tmp_are_empty_and_mounted_before_the_root(tmp_path):
    args = sandbox_args(tmp_path)
    tmpfs = [args[i + 1] for i, arg in enumerate(args) if arg == "--tmpfs"]

    assert tmpfs == ["/tmp", str(Path.home())]
    # The root is bound on top of them, in case it's inside one of them
    last_tmpfs = max(i for i, arg in enumerate(args) if arg == "--tmpfs")
    assert args.index("--bind") > last_tmpfs


def test_everything_but_the_network_is_unshared(tmp_path):
    args = sandbox_args(tmp_path)

    assert "--unshare-all" in args
    assert "--share-net" in args
    assert "--die-with-parent" in args
    assert "--new-session" in args


def test_sandbox_command_runs_bash_in_bwrap(tmp_path):
    command = sandbox_command(tmp_path, "ls -la")

    assert Path(command[0]).name == "bwrap"
    assert command[-3:] == ["bash", "-c", "ls -la"]


# --- sandbox_env -------------------------------------------------------------


@pytest.mark.usefixtures("no_uv")
def test_env_leaves_out_secrets(monkeypatch):
    monkeypatch.setenv("SYNAPSE_API_KEY", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    env = sandbox_env()

    assert "SYNAPSE_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["LANG"] == "en_US.UTF-8"
    assert env["PATH"] == sandbox.SANDBOX_PATH
    assert env["HOME"] == str(Path.home())


# --- uv ----------------------------------------------------------------------


def test_find_uv_in_home_directory(fake_uv):
    assert find_uv() == fake_uv


def test_find_uv_respects_uv_environment(fake_uv, monkeypatch, tmp_path):
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "my-cache"))
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "my-pythons"))

    uv = find_uv()

    assert uv is not None
    assert uv.cache_dir == tmp_path / "my-cache"
    assert uv.python_dir == tmp_path / "my-pythons"


def test_find_uv_in_system_directory_needs_no_mounts(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/uv")

    uv = find_uv()

    assert uv is not None
    assert uv.mounts == ()
    assert uv.bin_dir is None


def test_find_uv_in_opt_is_added_to_path(monkeypatch):
    # As on GitHub Actions: /opt is visible in the sandbox, but not on its PATH
    monkeypatch.setattr(
        sandbox.shutil, "which", lambda name: "/opt/hostedtoolcache/uv/0.9/x64/uv"
    )

    uv = find_uv()

    assert uv is not None
    assert uv.mounts == ()
    assert uv.bin_dir == Path("/opt/hostedtoolcache/uv/0.9/x64")
    assert sandbox_env()["PATH"].startswith("/opt/hostedtoolcache/uv/0.9/x64:")


def test_find_uv_mounts_the_target_of_a_link(monkeypatch, tmp_path):
    # A link in the home directory to uv elsewhere outside the system directories
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "uv").touch(mode=0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").symlink_to(real_dir / "uv")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: str(bin_dir / "uv"))

    uv = find_uv()

    assert uv is not None
    assert uv.mounts == (
        (real_dir / "uv", real_dir / "uv"),
        (real_dir / "uv", bin_dir / "uv"),
    )


def test_find_uv_without_uv(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)

    assert find_uv() is None


def test_uv_cache_and_pythons_are_overlaid(fake_uv, monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "supports_overlay", lambda: True)
    root = tmp_path / "project"

    args = sandbox_args(root)

    uv, uvx = (str(destination) for _, destination in fake_uv.mounts)
    assert option_values(args, "--ro-bind")[-2:] == [[uv, uv], [uvx, uvx]]
    overlays = [args[i + 1] for i, arg in enumerate(args) if arg == "--tmp-overlay"]
    assert overlays == [str(fake_uv.cache_dir), str(fake_uv.python_dir)]
    # Nothing of uv is writable on the host: the root is still the only bind
    assert option_values(args, "--bind") == [[str(root), str(root)]]
    assert args.index("--bind") > args.index("--tmp-overlay")


def test_uv_without_overlays_gets_an_empty_cache(fake_uv, monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "supports_overlay", lambda: False)

    args = sandbox_args(tmp_path / "project")

    assert "--tmp-overlay" not in args
    tmpfs = [args[i + 1] for i, arg in enumerate(args) if arg == "--tmpfs"]
    assert str(fake_uv.cache_dir) in tmpfs
    python_dir = str(fake_uv.python_dir)
    assert [python_dir, python_dir] in option_values(args, "--ro-bind")


def test_missing_uv_dirs_start_empty(fake_uv, monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "supports_overlay", lambda: True)
    fake_uv.python_dir.rmdir()

    args = sandbox_args(tmp_path / "project")

    tmpfs = [args[i + 1] for i, arg in enumerate(args) if arg == "--tmpfs"]
    assert str(fake_uv.python_dir) in tmpfs


def test_env_points_uv_at_its_mounts(fake_uv):
    env = sandbox_env()

    assert env["PATH"] == f"{fake_uv.bin_dir}:{sandbox.SANDBOX_PATH}"
    assert env["UV_CACHE_DIR"] == str(fake_uv.cache_dir)
    assert env["UV_PYTHON_INSTALL_DIR"] == str(fake_uv.python_dir)
    assert env["UV_LINK_MODE"] == "copy"


# --- sandbox_problem ---------------------------------------------------------


@pytest.mark.parametrize("root", [Path("/"), Path.home(), Path.home().parent])
def test_refuses_roots_that_contain_the_home_directory(root):
    problem = sandbox_problem(root)

    assert problem is not None
    assert "too broad" in problem


def test_refuses_root_that_contains_the_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    problem = sandbox_problem(tmp_path)

    assert problem is not None
    assert "contains tandemone's config" in problem


def test_root_inside_the_config_dir_is_fine(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(sandbox, "_probe", lambda root: None)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")

    assert sandbox_problem(tmp_path / "config" / "tandemone" / "project") is None


def test_reports_missing_bwrap(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)

    problem = sandbox_problem(tmp_path)

    assert problem is not None
    assert "bwrap isn't installed" in problem


@pytest.mark.usefixtures("no_uv")
def test_reports_sandbox_that_fails_to_start(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    run = MagicMock(
        return_value=subprocess.CompletedProcess(
            [], 1, stdout="", stderr="bwrap: setting up uid map: Permission denied\n"
        )
    )
    monkeypatch.setattr(sandbox.subprocess, "run", run)

    problem = sandbox_problem(tmp_path)

    assert problem == (
        "the sandbox couldn't start: bwrap: setting up uid map: Permission denied"
    )


@pytest.mark.usefixtures("no_uv")
def test_probe_runs_once_per_root(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(sandbox.subprocess, "run", run)

    assert sandbox_problem(tmp_path) is None
    assert sandbox_problem(tmp_path) is None

    run.assert_called_once()


# --- The real sandbox --------------------------------------------------------

needs_sandbox = pytest.mark.skipif(
    sandbox_problem(Path(tempfile.gettempdir())) is not None,
    reason="the bubblewrap sandbox isn't available here",
)


@pytest.fixture
def root(tmp_path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@needs_sandbox
def test_commands_can_change_files_in_the_root(root):
    result = run_bash("echo hi > file.txt && mkdir sub", root)

    assert result.exit_code == 0
    assert (root / "file.txt").read_text() == "hi\n"
    assert (root / "sub").is_dir()


@needs_sandbox
def test_files_outside_the_root_are_not_visible(root, tmp_path):
    (tmp_path / "secret.txt").write_text("secret\n")

    result = run_bash(f"cat {tmp_path / 'secret.txt'}", root)

    assert result.exit_code != 0
    assert "secret" not in result.stdout


@needs_sandbox
def test_writes_outside_the_root_are_not_kept(root, tmp_path):
    outside = tmp_path / "escaped.txt"

    run_bash(f"touch {outside} ../escaped2.txt ~/tandemone-escaped.txt", root)

    assert not outside.exists()
    assert not (tmp_path / "escaped2.txt").exists()
    assert not (Path.home() / "tandemone-escaped.txt").exists()


@needs_sandbox
def test_system_dirs_are_read_only_inside(root):
    result = run_bash("touch /usr/tandemone-test", root)

    assert result.exit_code != 0
    assert "Read-only file system" in result.stderr


@needs_sandbox
def test_symlinks_out_of_the_root_lead_nowhere(root, tmp_path):
    (tmp_path / "secret.txt").write_text("secret\n")
    (root / "link").symlink_to(tmp_path / "secret.txt")

    result = run_bash("cat link", root)

    assert result.exit_code != 0
    assert "secret" not in result.stdout


@needs_sandbox
def test_host_environment_is_not_passed(root, monkeypatch):
    monkeypatch.setenv("SYNAPSE_API_KEY", "secret")

    result = run_bash('echo "${SYNAPSE_API_KEY:-unset}"', root)

    assert result.stdout == "unset\n"


@needs_sandbox
def test_host_processes_are_not_visible(root):
    # In a new PID namespace, the command is one of only a few processes
    result = run_bash("ls /proc | grep -c '^[0-9]'", root)

    assert int(result.stdout) < 10


needs_uv = pytest.mark.skipif(find_uv() is None, reason="uv isn't installed")


@needs_sandbox
@needs_uv
def test_uv_runs_in_the_sandbox(root):
    result = run_bash("uv --version && uvx --version", root)

    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("uv ")


@needs_sandbox
@needs_uv
def test_writes_to_the_uv_cache_are_not_kept(root):
    uv = find_uv()
    assert uv is not None
    planted = uv.cache_dir / "tandemone-planted"

    run_bash(f"mkdir -p {uv.cache_dir} && touch {planted}", root)

    assert not planted.exists()
