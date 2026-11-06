from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class TandemOneSettings(BaseSettings):
    """Settings for the tandemone CLI."""

    SYNAPSE_API_KEY: str
    # Base URL of the SynapseAPI server, e.g. https://synapse.example.com
    SYNAPSE_API_URL: str

    @field_validator("SYNAPSE_API_URL")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("must start with https:// or http://")
        return value


# Find .env file in project root
project_root = Path(__file__).resolve().parent.parent.parent.parent

tocli_settings = TandemOneSettings(
    _env_file=str(project_root / ".env"), _env_file_encoding="utf-8"
)
