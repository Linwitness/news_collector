"""
llm.py — Build the LLM client for the provider selected in settings.json.

settings["active_llm"] picks an entry from settings["llm_providers"]. Every
provider is reached through the anthropic SDK (DeepSeek exposes an
Anthropic-compatible endpoint), so only key, base_url and model names differ.
"""

import anthropic
import os


def get_llm(settings: dict) -> tuple:
    """Return (client, model, translation_model, provider_name)."""
    name = settings["active_llm"]
    cfg = settings["llm_providers"][name]

    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        raise EnvironmentError(
            f"Environment variable {cfg['api_key_env']} is not set "
            f"(required by active_llm '{name}')."
        )

    client = anthropic.Anthropic(
        api_key=api_key, base_url=cfg.get("base_url") or None, max_retries=5
    )
    model = cfg["model"]
    return client, model, cfg.get("translation_model", model), name


def response_text(message) -> str:
    """Join the text blocks of a response, skipping thinking blocks (DeepSeek emits them first)."""
    return "".join(b.text for b in message.content if b.type == "text")
