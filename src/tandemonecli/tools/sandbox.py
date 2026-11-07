"""The sandbox the model's commands run in, built with bubblewrap (``bwrap``).

Inside the sandbox:

- The project root is the only directory that is writable and kept.
- System directories (/usr, /etc, /opt) are read-only, so programs, DNS and TLS work.
- The user's home directory and /tmp are empty and thrown away after each command,
  so the rest of the user's files can't be read or changed.
- Processes, IPC and the hostname are separate from the host. The network is shared,
  so installs, git and API calls work.
- Only a few harmless environment variables are passed, so secrets in the user's
  environment (such as SYNAPSE_API_KEY) don't leak into commands.
- uv is available even if it's installed in the home directory: the ``uv`` and
  ``uvx`` programs read-only, and uv's cache and Python installs as throwaway
  overlays. Commands read the real ones, but what they write there is thrown away,
  so they can't plant packages that uv later uses outside the sandbox.

If the sandbox can't be used, commands don't run at all: there is no fallback that
runs them unsandboxed.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from tandemonecli.config.paths import user_config_dir

BWRAP = "bwrap"

# Mounted read-only if they exist
_READ_ONLY_DIRS = ("/usr", "/etc", "/opt")
# Where systemd-resolved keeps resolv.conf on some distros (/etc/resolv.conf links here)
_RESOLVED_DIR = "/run/systemd/resolve"
# Usually symlinks into /usr (merged /usr); otherwise mounted read-only
_TOP_LEVEL_SYSTEM_DIRS = ("/bin", "/sbin", "/lib", "/lib32", "/lib64")

SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# Passed from the user's environment if set; everything else is left out
_PASSED_ENV = (
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TZ",
    "USER",
    "LOGNAME",
)

# Starting the sandbox to check it works shouldn't take long
_PROBE_TIMEOUT = 10

_UV_PROGRAMS = ("uv", "uvx")


@dataclass(frozen=True)
class UvSetup:
    """Where uv is on the host, so the sandbox can make it available."""

    # The uv programs to mount read-only (empty if they're already visible)
    programs: tuple[Path, ...]
    # Their directory, added to PATH (None if it's already on the sandbox's PATH)
    bin_dir: Path | None
    cache_dir: Path
    python_dir: Path


def find_uv() -> UvSetup | None:
    """Return where uv and its directories are on the host, or None without uv.

    The directories are worked out from the environment the way uv does by
    default. uv itself isn't asked, because it would read settings from the
    project, which commands can change.
    """
    found = shutil.which("uv")
    if found is None:
        return None
    bin_dir = Path(found).absolute().parent
    if _is_system_path(bin_dir):
        programs: tuple[Path, ...] = ()
        extra_bin_dir = None
    else:
        programs = tuple(
            bin_dir / name for name in _UV_PROGRAMS if (bin_dir / name).is_file()
        )
        extra_bin_dir = bin_dir

    home = Path.home()
    cache_home = os.environ.get("XDG_CACHE_HOME") or home / ".cache"
    data_home = os.environ.get("XDG_DATA_HOME") or home / ".local" / "share"
    cache_dir = os.environ.get("UV_CACHE_DIR") or Path(cache_home) / "uv"
    python_dir = (
        os.environ.get("UV_PYTHON_INSTALL_DIR") or Path(data_home) / "uv" / "python"
    )
    return UvSetup(
        programs=programs,
        bin_dir=extra_bin_dir,
        cache_dir=Path(cache_dir).absolute(),
        python_dir=Path(python_dir).absolute(),
    )


def _is_system_path(path: Path) -> bool:
    """Whether ``path`` is in a system directory, which the sandbox shows anyway."""
    return any(
        path.is_relative_to(directory)
        for directory in (*_READ_ONLY_DIRS, *_TOP_LEVEL_SYSTEM_DIRS)
    )


@cache
def supports_overlay() -> bool:
    """Whether bwrap can mount throwaway overlays (bubblewrap 0.10 and later)."""
    bwrap = shutil.which(BWRAP)
    if bwrap is None:
        return False
    try:
        completed = subprocess.run(
            [bwrap, "--help"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_PROBE_TIMEOUT,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return "--tmp-overlay" in completed.stdout + completed.stderr


def _uv_args(uv: UvSetup) -> list[str]:
    """Return the bwrap options that make uv available in the sandbox."""
    args: list[str] = []
    for program in uv.programs:
        args += ["--ro-bind", str(program.resolve()), str(program)]
    for directory, read_only_fallback in ((uv.cache_dir, False), (uv.python_dir, True)):
        if directory.is_dir() and supports_overlay():
            # Reads come from the real directory, writes go to a throwaway layer
            args += ["--overlay-src", str(directory), "--tmp-overlay", str(directory)]
        elif directory.is_dir() and read_only_fallback:
            args += ["--ro-bind", str(directory), str(directory)]
        else:
            # Missing, or a cache that can't be overlaid: start empty
            args += ["--tmpfs", str(directory)]
    return args


def sandbox_args(root: Path) -> list[str]:
    """Return the bwrap options that confine a command to ``root``.

    Mounts are applied in order, so uv and the root are mounted on top of the empty
    home directory and /tmp in case they're inside one of them. The root comes
    last.
    """
    args: list[str] = []
    for directory in _READ_ONLY_DIRS:
        args += ["--ro-bind-try", directory, directory]
    for directory in _TOP_LEVEL_SYSTEM_DIRS:
        if os.path.islink(directory):
            args += ["--symlink", os.readlink(directory), directory]
        elif os.path.isdir(directory):
            args += ["--ro-bind", directory, directory]
    args += ["--ro-bind-try", _RESOLVED_DIR, _RESOLVED_DIR]
    args += [
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", str(Path.home()),
    ]  # fmt: skip
    if uv := find_uv():
        args += _uv_args(uv)
    args += [
        "--bind", str(root), str(root),
        "--chdir", str(root),
        # New namespaces for everything except the network
        "--unshare-all",
        "--share-net",
        # Stop the command when the CLI exits, and keep it off the user's terminal
        "--die-with-parent",
        "--new-session",
    ]  # fmt: skip
    return args


def sandbox_command(root: Path, command: str) -> list[str]:
    """Return the full command line that runs ``command`` with bash in the sandbox."""
    bwrap = shutil.which(BWRAP) or BWRAP
    return [bwrap, *sandbox_args(root), "bash", "-c", command]


def sandbox_env() -> dict[str, str]:
    """Return the environment for sandboxed commands."""
    env = {name: os.environ[name] for name in _PASSED_ENV if name in os.environ}
    env["PATH"] = SANDBOX_PATH
    env["HOME"] = str(Path.home())
    if uv := find_uv():
        if uv.bin_dir is not None:
            env["PATH"] = f"{uv.bin_dir}:{SANDBOX_PATH}"
        env["UV_CACHE_DIR"] = str(uv.cache_dir)
        env["UV_PYTHON_INSTALL_DIR"] = str(uv.python_dir)
        # The cache is on another file system inside the sandbox, so hard links fail
        env["UV_LINK_MODE"] = "copy"
    return env


def sandbox_problem(root: Path) -> str | None:
    """Return why commands can't run sandboxed in ``root``, or None if they can.

    The root can't be / or the home directory (or a parent of it), because then
    the sandbox would give access to all of the user's files. It also can't contain
    tandemone's config, or commands could change which commands are allowed.
    """
    home = Path.home().resolve()
    resolved = root.resolve()
    if resolved == Path("/") or resolved == home or resolved in home.parents:
        return (
            f"{resolved} is too broad to sandbox, because commands could change all "
            "your files. Start tandemone in a project directory instead."
        )
    if user_config_dir().resolve().is_relative_to(resolved):
        return (
            f"{resolved} contains tandemone's config ({user_config_dir()}), which "
            "commands mustn't change. Start tandemone in a project directory instead."
        )
    if shutil.which(BWRAP) is None:
        return (
            "commands run in a bubblewrap sandbox, but bwrap isn't installed. "
            "Install the bubblewrap package to let the model run commands."
        )
    return _probe(resolved)


@cache
def _probe(root: Path) -> str | None:
    """Start the sandbox once to check it works here, for example that the kernel
    allows the namespaces it needs."""
    try:
        completed = subprocess.run(
            sandbox_command(root, "true"),
            env=sandbox_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"the sandbox couldn't start: {e}"
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        return f"the sandbox couldn't start: {detail}"
    return None
