from pathlib import Path

import pytest
from pydantic import ValidationError

from tandemonecli.config.settings import TandemOneSettings, tocli_settings


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
