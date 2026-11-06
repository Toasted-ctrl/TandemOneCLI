from dataclasses import dataclass
from uuid import UUID

import requests

from tandemonecli.config.settings import tocli_settings


@dataclass(frozen=True)
class User:
    user_id: UUID
    first_name: str
    last_name: str


def get_user(api_key: str) -> User:
    headers: dict = {"X-API-Key": api_key}
    response = requests.get(
        url=f"{tocli_settings.SYNAPSE_API_URL}/api/v1/user/me", headers=headers
    )
    response.raise_for_status()
    data = response.json()
    return User(
        user_id=UUID(data.get("user_id")),
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
    )
