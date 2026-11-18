"""The selection tandemone starts with, saved with ``/set-default``.

Saved in ``~/.config/tandemone/defaults.json``. At startup, once the available
models are known, the provider and model are selected only if that model is
still available; web search is always applied.

The max output tokens aren't saved: a limit that suits one model can be more
than another supports, which makes every request fail.
"""

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from tandemonecli.config.paths import user_config_dir

DEFAULTS_FILE = user_config_dir() / "defaults.json"


class Defaults(BaseModel):
    """The contents of defaults.json."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    web: bool = False

    @model_validator(mode="before")
    @classmethod
    def drop_max_output_tokens(cls, data: Any) -> Any:
        """Ignore the max output tokens that earlier versions saved."""
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "max_output_tokens"}
        return data


class DefaultsError(Exception):
    """defaults.json can't be read or saved."""


def load_defaults(path: Path | None = None) -> Defaults:
    """Read the defaults file; without one, nothing is selected."""
    path = path or DEFAULTS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Defaults()
    except OSError as e:
        raise DefaultsError(f"can't read {path}: {e.strerror}") from None
    try:
        return Defaults.model_validate_json(text)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'file'}: {error['msg']}"
            for error in e.errors()
        )
        raise DefaultsError(f"{path} isn't valid: {problems}") from None


def save_defaults(defaults: Defaults, path: Path | None = None) -> None:
    """Write the defaults file, replacing it in one step so it's never half written."""
    path = path or DEFAULTS_FILE
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(defaults.model_dump_json(indent=2) + "\n", "utf-8")
        os.replace(temporary, path)
    except OSError as e:
        raise DefaultsError(f"can't save {path}: {e.strerror}") from None
