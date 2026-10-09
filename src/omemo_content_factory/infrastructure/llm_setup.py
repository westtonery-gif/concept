"""Writing the model choice into ``.env`` — one block, every role (ADR-0094 §5).

``env_block`` turns an operator's choice (provider, model, rates, budget) into the
``OMEMO_<NAME>__DEFAULT`` lines selection reads, and :func:`problem_with` runs that very block
through selection with a placeholder key, so a typo is reported before it is written. No rate is
invented: the operator supplies both prices (or states the model is free), exactly as ADR-0029
demands; the API key is never written, only named.
"""

from __future__ import annotations

from dataclasses import dataclass

from omemo_content_factory.infrastructure.provider_model import (
    PROVIDER_PRESETS,
    ProviderModelSelectionError,
    client_for_role,
)

__all__ = ["BLOCK_NAME", "LLMChoice", "env_block", "key_variable", "problem_with"]

BLOCK_NAME = "factory model (configure_llm.py)"


@dataclass(frozen=True, slots=True)
class LLMChoice:
    """One model for every role. Rates are per one million tokens, as the provider prints them."""

    provider: str
    model: str
    input_price: str
    output_price: str
    currency: str = "USD"
    max_tokens: int = 16000
    thinking: str = "inherit"
    base_url: str | None = None
    key_env: str | None = None
    wire: str | None = None
    structured: str | None = None


def key_variable(choice: LLMChoice) -> str | None:
    """The environment variable the key must be in, or ``None`` when no key is needed."""
    if choice.key_env:
        return choice.key_env
    if choice.provider == "anthropic":
        return "ANTHROPIC_API_KEY"
    preset = PROVIDER_PRESETS.get(choice.provider)
    return preset.key_env if preset else None


def env_block(choice: LLMChoice) -> list[str]:
    """The ``.env`` lines for ``choice``; optional settings appear only when given."""
    lines = [
        f"OMEMO_PROVIDER__DEFAULT={choice.provider}",
        f"OMEMO_MODEL__DEFAULT={choice.model}",
        f"OMEMO_INPUT_PRICE_PER_MILLION__DEFAULT={choice.input_price}",
        f"OMEMO_OUTPUT_PRICE_PER_MILLION__DEFAULT={choice.output_price}",
        f"OMEMO_PRICE_CURRENCY__DEFAULT={choice.currency}",
        f"OMEMO_MAX_TOKENS__DEFAULT={choice.max_tokens}",
        f"OMEMO_THINKING__DEFAULT={choice.thinking}",
    ]
    optional = (
        ("OMEMO_BASE_URL__DEFAULT", choice.base_url),
        ("OMEMO_API_KEY_ENV__DEFAULT", choice.key_env),
        ("OMEMO_WIRE__DEFAULT", choice.wire),
        ("OMEMO_STRUCTURED__DEFAULT", choice.structured),
    )
    lines.extend(f"{name}={value}" for name, value in optional if value)
    key = key_variable(choice)
    if key:
        lines.append(f"# the key is yours to add, outside this block:  {key}=...")
    return lines


def problem_with(choice: LLMChoice) -> str | None:
    """What selection would refuse about ``choice``, or ``None`` if it is usable.

    The block is resolved through selection for a probe role with a placeholder key, so only a
    genuine configuration fault is reported (a missing real key is the doctor's business).
    """
    environ: dict[str, str] = {}
    scratch = {line.partition("=")[0]: line.partition("=")[2] for line in env_block(choice)}
    for name, value in scratch.items():
        if not name.startswith("#"):
            environ[name] = value
    key = key_variable(choice)
    if key:
        environ[key] = "placeholder-key"
    try:
        client_for_role("probe@v1", environ)
    except ProviderModelSelectionError as error:
        return str(error)
    return None
