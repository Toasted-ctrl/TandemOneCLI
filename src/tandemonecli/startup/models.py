from dataclasses import dataclass
from typing import Dict, List

import requests

from tandemonecli.config.settings import tocli_settings


@dataclass(frozen=True)
class AvailableModels:
    """Container for available chat completion models organized by provider."""

    providers: Dict[str, List[str]]


def get_models(api_key: str) -> AvailableModels:
    """Fetch all available models and return only chat_completion models per provider."""

    headers: dict = {"X-API-Key": api_key}
    response = requests.get(
        url=f"{tocli_settings.SYNAPSE_API_URL}/api/v1/providers/models",
        headers=headers,
    )
    response.raise_for_status()
    data = response.json()

    # Extract chat_completion models for each provider
    providers_models = {}
    for provider_name, model_types in data["providers"].items():
        if "chat_completion" in model_types and model_types["chat_completion"]:
            providers_models[provider_name] = model_types["chat_completion"]

    return AvailableModels(providers=providers_models)
