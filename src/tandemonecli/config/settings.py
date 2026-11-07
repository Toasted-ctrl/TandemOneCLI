from pathlib import Path

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings

from tandemonecli.config.paths import user_config_dir


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


# The user config works wherever tandemone is installed. The .env in the project
# root (found from this file, so only in a source checkout) is kept for development.
USER_ENV_FILE = user_config_dir() / ".env"
REPO_ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"


def load_settings() -> TandemOneSettings:
    """Load the settings, or exit with a readable message if they're incomplete.

    Environment variables come first, then the user config, then the project's .env.
    Missing files are skipped.
    """
    try:
        # Later files take precedence over earlier ones
        return TandemOneSettings(
            _env_file=(REPO_ENV_FILE, USER_ENV_FILE), _env_file_encoding="utf-8"
        )
    except ValidationError as e:
        problems = "\n".join(
            f"  - {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in e.errors()
        )
        raise SystemExit(
            "Tandem One CLI isn't configured correctly:\n"
            f"{problems}\n\n"
            f"Set SYNAPSE_API_KEY and SYNAPSE_API_URL in {USER_ENV_FILE}\n"
            "(or as environment variables). See .env.example for the format."
        ) from None


tocli_settings = load_settings()
