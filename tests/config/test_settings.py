from pathlib import Path

import pytest
from pydantic import ValidationError

from tandemonecli.config import settings
from tandemonecli.config.settings import (
    TandemOneSettings,
    load_settings,
    tocli_settings,
    user_config_dir,
)


def test_reads_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("SYNAPSE_API_KEY", "from-env")

    settings = TandemOneSettings(_env_file=None)

    assert settings.SYNAPSE_API_KEY == "from-env"


def test_reads_api_key_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SYNAPSE_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SYNAPSE_API_KEY=from-file\n", encoding="utf-8")

    settings = TandemOneSettings(_env_file=str(env_file))

    assert settings.SYNAPSE_API_KEY == "from-file"


def test_environment_takes_precedence_over_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SYNAPSE_API_KEY", "from-env")
    env_file = tmp_path / ".env"
    env_file.write_text("SYNAPSE_API_KEY=from-file\n", encoding="utf-8")

    settings = TandemOneSettings(_env_file=str(env_file))

    assert settings.SYNAPSE_API_KEY == "from-env"


def test_missing_api_key_raises_validation_error(monkeypatch):
    monkeypatch.delenv("SYNAPSE_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        TandemOneSettings(_env_file=None)


def test_module_level_settings_is_loaded():
    assert isinstance(tocli_settings, TandemOneSettings)
    assert tocli_settings.SYNAPSE_API_KEY


# --- SYNAPSE_API_URL ---------------------------------------------------------


def test_reads_api_url_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SYNAPSE_API_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SYNAPSE_API_KEY=k\nSYNAPSE_API_URL=https://from-file.test\n",
        encoding="utf-8",
    )

    settings = TandemOneSettings(_env_file=str(env_file))

    assert settings.SYNAPSE_API_URL == "https://from-file.test"


def test_missing_api_url_raises_validation_error(monkeypatch):
    monkeypatch.delenv("SYNAPSE_API_URL", raising=False)

    with pytest.raises(ValidationError, match="SYNAPSE_API_URL"):
        TandemOneSettings(_env_file=None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://synapse.test", "https://synapse.test"),
        ("https://synapse.test/", "https://synapse.test"),
        ("  https://synapse.test//  ", "https://synapse.test"),
        ("http://localhost:8000", "http://localhost:8000"),
    ],
)
def test_api_url_is_normalised(monkeypatch, value, expected):
    monkeypatch.setenv("SYNAPSE_API_URL", value)

    assert TandemOneSettings(_env_file=None).SYNAPSE_API_URL == expected


@pytest.mark.parametrize("value", ["synapse.test", "ftp://synapse.test", ""])
def test_api_url_without_http_scheme_is_rejected(monkeypatch, value):
    monkeypatch.setenv("SYNAPSE_API_URL", value)

    with pytest.raises(ValidationError, match="https:// or http://"):
        TandemOneSettings(_env_file=None)


def test_example_env_file_lists_every_setting():
    example = Path(__file__).parents[2] / ".env.example"
    names = {
        line.split("=", 1)[0]
        for line in example.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }

    assert names == set(TandemOneSettings.model_fields)


# --- Config file locations ---------------------------------------------------


def test_user_config_dir_follows_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert user_config_dir() == tmp_path / "tandemone"


def test_user_config_dir_defaults_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert user_config_dir() == tmp_path / ".config" / "tandemone"


@pytest.fixture
def env_files(monkeypatch, tmp_path) -> tuple[Path, Path]:
    """Point the settings at a repo and a user .env in a temporary directory."""
    monkeypatch.delenv("SYNAPSE_API_KEY", raising=False)
    monkeypatch.delenv("SYNAPSE_API_URL", raising=False)
    repo_env = tmp_path / "repo.env"
    user_env = tmp_path / "user.env"
    monkeypatch.setattr(settings, "REPO_ENV_FILE", repo_env)
    monkeypatch.setattr(settings, "USER_ENV_FILE", user_env)
    return repo_env, user_env


def test_load_settings_reads_user_config(env_files):
    _, user_env = env_files
    user_env.write_text(
        "SYNAPSE_API_KEY=from-user\nSYNAPSE_API_URL=https://user.test\n",
        encoding="utf-8",
    )

    loaded = load_settings()

    assert loaded.SYNAPSE_API_KEY == "from-user"
    assert loaded.SYNAPSE_API_URL == "https://user.test"


def test_load_settings_user_config_overrides_repo_env(env_files):
    repo_env, user_env = env_files
    repo_env.write_text(
        "SYNAPSE_API_KEY=from-repo\nSYNAPSE_API_URL=https://repo.test\n",
        encoding="utf-8",
    )
    user_env.write_text("SYNAPSE_API_KEY=from-user\n", encoding="utf-8")

    loaded = load_settings()

    assert loaded.SYNAPSE_API_KEY == "from-user"
    # Settings missing from the user config still come from the repo .env
    assert loaded.SYNAPSE_API_URL == "https://repo.test"


def test_load_settings_exits_with_readable_message(env_files):
    _, user_env = env_files

    with pytest.raises(SystemExit) as exc_info:
        load_settings()

    message = str(exc_info.value.code)
    assert "SYNAPSE_API_KEY" in message
    assert "SYNAPSE_API_URL" in message
    assert str(user_env) in message
