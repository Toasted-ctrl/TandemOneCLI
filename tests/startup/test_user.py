from unittest.mock import MagicMock
from uuid import UUID

import pytest
import requests

from tandemonecli.startup import user as user_module
from tandemonecli.startup.user import User, get_user

USER_ID = "12345678-1234-5678-1234-567812345678"


@pytest.fixture
def mock_get(monkeypatch, make_response) -> MagicMock:
    mock = MagicMock(
        return_value=make_response(
            {"user_id": USER_ID, "first_name": "Ada", "last_name": "Lovelace"}
        )
    )
    monkeypatch.setattr(user_module.requests, "get", mock)
    return mock


def test_returns_user_from_response(mock_get):
    user = get_user("secret-key")

    assert user == User(user_id=UUID(USER_ID), first_name="Ada", last_name="Lovelace")


def test_user_id_is_parsed_as_uuid(mock_get):
    user = get_user("secret-key")

    assert isinstance(user.user_id, UUID)


def test_calls_user_endpoint_with_api_key_header(mock_get):
    get_user("secret-key")

    mock_get.assert_called_once_with(
        url="https://synapse.test/api/v1/user/me",
        headers={"X-API-Key": "secret-key"},
    )


def test_http_error_is_raised(monkeypatch, make_response):
    monkeypatch.setattr(
        user_module.requests,
        "get",
        MagicMock(return_value=make_response({}, status_code=401)),
    )

    with pytest.raises(requests.HTTPError):
        get_user("bad-key")


def test_user_is_immutable(mock_get):
    user = get_user("secret-key")

    with pytest.raises(AttributeError):
        user.first_name = "Changed"  # type: ignore[misc]
