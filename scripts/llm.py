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
    # Extra request params (e.g. thinking) for analysis and translation calls.
    extra: dict = field(default_factory=dict)
    translation_extra: dict = field(default_factory=dict)


def _thinking_param(value) -> dict:
    """Map a provider "thinking" setting to request params.

    false -> disabled; a dict is sent as-is (e.g. {"type": "adaptive"});
    absent -> the provider's default. DeepSeek thinks by default and the
    thinking counts against max_tokens, which can leave no room for the report.
    """
    if value is False:
        return {"thinking": {"type": "disabled"}}
    if isinstance(value, dict):
        return {"thinking": value}
    return {}


def get_llm(settings: dict, task: str = None) -> LLM:
    """Build the LLM handle for settings["active_llm"].

    task (e.g. "weekly") merges the provider's matching override block into the
    analysis settings; translation always uses the base settings.
    """
    name = settings["active_llm"]
    base = settings["llm_providers"][name]
    cfg = {**base, **base.get(task, {})} if task else base

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

    return LLM(
        client=client,
        provider=name,
        model=model,
        translation_model=base.get("translation_model", base["model"]),
        max_tokens=cfg.get("max_tokens", 8000),
        translation_max_tokens=base.get("translation_max_tokens", 12000),
        extra=_thinking_param(cfg.get("thinking")),
        translation_extra=_thinking_param(base.get("thinking")),
    )


def create(llm: LLM, prompt: str, translation: bool = False) -> str:
    """Send one single-turn request and return its text.

    Streams so large max_tokens values don't hit the SDK's non-streaming limit
    or HTTP timeouts.
    """
    with llm.client.messages.stream(
        model=llm.translation_model if translation else llm.model,
        max_tokens=llm.translation_max_tokens if translation else llm.max_tokens,
        messages=[{"role": "user", "content": prompt}],
        **(llm.translation_extra if translation else llm.extra),
    ) as stream:
        message = stream.get_final_message()
    return response_text(message)


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
