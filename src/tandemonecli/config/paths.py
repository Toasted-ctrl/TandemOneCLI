import os
from pathlib import Path


def user_config_dir() -> Path:
    """Return the user's config directory for tandemone (XDG, ~/.config by default)."""
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "tandemone"
