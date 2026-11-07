"""Shared test fixtures.

``tandemonecli.config.settings`` builds its settings object at import time, so
``SYNAPSE_API_KEY`` and ``SYNAPSE_API_URL`` must be set before any ``tandemonecli``
module is imported.
pytest loads this file before it collects the test modules, so setting it here
lets the suite run without a real ``.env`` file (for example in CI).
"""

import os
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

# Always use dummy values, even if real ones are in .env or the environment
os.environ["SYNAPSE_API_KEY"] = "test-api-key"
os.environ["SYNAPSE_API_URL"] = "https://synapse.test"

from tandemonecli import permissions  # noqa: E402
from tandemonecli.startup.models import AvailableModels  # noqa: E402


@pytest.fixture(autouse=True)
def permissions_file(monkeypatch, tmp_path) -> Path:
    """Use a temporary permissions file, never the user's real one."""
    path = tmp_path / "config" / "permissions.json"
    monkeypatch.setattr(permissions, "PERMISSIONS_FILE", path)
    return path


@pytest.fixture
def available_models() -> AvailableModels:
    return AvailableModels(
        providers={
            "openai": ["gpt-a", "gpt-b"],
            "anthropic": ["claude-a"],
        }
    )


@pytest.fixture
def make_response() -> Callable[..., MagicMock]:
    """Factory for fake ``requests.Response`` objects with JSON and a status code."""

    def factory(json_data: dict, status_code: int = 200) -> MagicMock:
        response = MagicMock(spec=requests.Response)
        response.status_code = status_code
        response.json.return_value = json_data
        if status_code >= 400:
            response.raise_for_status.side_effect = requests.HTTPError(
                f"{status_code} Error", response=response
            )
        return response

    return factory
