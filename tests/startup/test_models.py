from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
import requests

from tandemonecli.startup import models as models_module
from tandemonecli.startup.models import AvailableModels, get_models


@pytest.fixture
def patch_get(monkeypatch, make_response) -> Callable[..., MagicMock]:
    """Patch ``requests.get`` to return the given JSON and status code."""

    def patch(json_data: dict, status_code: int = 200) -> MagicMock:
        mock = MagicMock(return_value=make_response(json_data, status_code))
        monkeypatch.setattr(models_module.requests, "get", mock)
        return mock

    return patch


def test_returns_chat_completion_models_per_provider(patch_get):
    patch_get(
        {
            "providers": {
                "openai": {
                    "chat_completion": ["gpt-a", "gpt-b"],
                    "embedding": ["embed-a"],
                },
                "anthropic": {"chat_completion": ["claude-a"]},
            }
        },
    )

    models = get_models("secret-key")

    assert models == AvailableModels(
        providers={"openai": ["gpt-a", "gpt-b"], "anthropic": ["claude-a"]}
    )


def test_skips_providers_without_chat_completion_models(patch_get):
    patch_get(
        {
            "providers": {
                "openai": {"chat_completion": ["gpt-a"]},
                "embed-only": {"embedding": ["embed-a"]},
                "empty": {"chat_completion": []},
            }
        },
    )

    models = get_models("secret-key")

    assert models.providers == {"openai": ["gpt-a"]}


def test_returns_empty_when_no_providers(patch_get):
    patch_get({"providers": {}})

    models = get_models("secret-key")

    assert models.providers == {}


def test_calls_models_endpoint_with_api_key_header(patch_get):
    mock_get = patch_get({"providers": {}})

    get_models(api_key="secret-key")

    mock_get.assert_called_once_with(
        url="https://synapse.test/api/v1/providers/models",
        headers={"X-API-Key": "secret-key"},
    )


def test_http_error_is_raised(patch_get):
    patch_get({}, status_code=500)

    with pytest.raises(requests.HTTPError):
        get_models("secret-key")
