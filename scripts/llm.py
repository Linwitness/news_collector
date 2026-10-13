"""
llm.py — Build the LLM client for the provider selected in settings.json.

settings["active_llm"] picks an entry from settings["llm_providers"]. Every
provider is reached through the anthropic SDK (DeepSeek exposes an
Anthropic-compatible endpoint), so only key, base_url and model names differ.
"""

import anthropic
import os
from dataclasses import dataclass, field


@dataclass
class LLM:
    client: anthropic.Anthropic
    provider: str
    model: str
    translation_model: str
    max_tokens: int
    translation_max_tokens: int
    # Must be passed to every client.messages.create call.
    extra: dict = field(default_factory=dict)


def get_llm(settings: dict) -> LLM:
    """Build the LLM handle for settings["active_llm"]."""
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

    # DeepSeek thinks by default and the thinking counts against max_tokens,
    # which can leave no room for the report itself.
    extra = {}
    if cfg.get("thinking") is False:
        extra["thinking"] = {"type": "disabled"}

    return LLM(
        client=client,
        provider=name,
        model=model,
        translation_model=cfg.get("translation_model", model),
        # Kept <= ~21000: the SDK rejects larger non-streaming requests.
        max_tokens=cfg.get("max_tokens", 8000),
        translation_max_tokens=cfg.get("translation_max_tokens", 12000),
        extra=extra,
    )


def response_text(message) -> str:
    """Join the text blocks of a response, skipping thinking blocks.

    Raises if there is no text, so callers retry instead of writing an empty report.
    """
    text = "".join(b.text for b in message.content if b.type == "text")
    if not text.strip():
        raise RuntimeError(
            f"LLM returned no text (stop_reason={message.stop_reason}, "
            f"output_tokens={message.usage.output_tokens})"
        )
    if message.stop_reason == "max_tokens":
        print(f"  WARNING: response truncated at max_tokens ({message.usage.output_tokens} tokens)")
    return text
