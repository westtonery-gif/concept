"""Provider/model selection ownership — Content Factory owns which provider/model a role uses.

Realizes `ADR-0016` / `PROVIDER_MODEL_SPEC`. The **single public contract** is
:func:`client_for_role` — ``role + environment -> ready LLMClient`` (SPEC §4.1) — behind the
existing port (`ADR-0014`). Keyless is the **fake** provider; a missing or invalid binding **fails
closed** — never a silent hardcoded model default (PROJECT §5, §10). Reuses ``AnthropicLLMClient`` /
``FakeLLMClient``; the Composition Root still *receives* the resolved client and gains no selection
logic. ADR-0029's measured port result does not change selection ownership.

The ``role -> (provider, model, pricing, max_tokens, thinking)`` binding, its env format, and the
two-step load/resolve are **implementation details** (SPEC §1.2): provider granularity is per-role,
values come from the environment, and API keys are never read here (the adapter's SDK obtains them;
PROJECT §5, §6). Real-provider pricing is explicit and fail-closed (`ADR-0029`); so are the output
budget and the extended-thinking choice (`ADR-0052`). No rate, budget or thinking default is
hardcoded — each would be a guess about the role's model.
Callers depend only on :func:`client_for_role` and :class:`ProviderModelSelectionError`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.llm import (
    AnthropicLLMClient,
    LLMClient,
    ThinkingSetting,
    TokenPricing,
)

__all__ = ["ProviderModelSelectionError", "client_for_role"]

_PROVIDER_ANTHROPIC = "anthropic"
_PROVIDER_FAKE = "fake"
_ENV_PROVIDER_PREFIX = "OMEMO_PROVIDER__"
_ENV_MODEL_PREFIX = "OMEMO_MODEL__"
_ENV_INPUT_PRICE_PREFIX = "OMEMO_INPUT_PRICE_PER_MILLION__"
_ENV_OUTPUT_PRICE_PREFIX = "OMEMO_OUTPUT_PRICE_PER_MILLION__"
_ENV_PRICE_CURRENCY_PREFIX = "OMEMO_PRICE_CURRENCY__"
_ENV_MAX_TOKENS_PREFIX = "OMEMO_MAX_TOKENS__"
_ENV_THINKING_PREFIX = "OMEMO_THINKING__"


class ProviderModelSelectionError(Exception):
    """A role has no valid provider/model binding — selection fails closed (SPEC §4.2)."""


def client_for_role(agent_ref: str, environ: Mapping[str, str]) -> LLMClient:
    """Return the ready :class:`LLMClient` for a role's configured provider/model (SPEC §4.1).

    The **single public entry**: ``role + environment -> client``. Config assembly and the internal
    ``role -> binding`` representation are hidden; callers depend only on this contract and the
    ``LLMClient`` port. Fail-closed (SPEC §4.2): a missing / unknown / model-less binding raises
    :class:`ProviderModelSelectionError` — never a silent default.
    """
    config = _load_config_from_env(environ, [agent_ref])
    return _resolve_client(agent_ref, config)


# --- implementation details (not part of the public contract) -----------------------------


@dataclass(frozen=True, slots=True)
class _RoleBinding:
    """Internal: provider/model, explicit pricing (ADR-0029 §2), output budget and thinking choice.

    ``max_tokens`` and ``thinking`` are part of the binding, not of the adapter's defaults
    (`ADR-0052` §1-§2): the parameter deciding whether an answer fits is configuration, per role.
    """

    provider: str
    model: str = ""
    input_price_per_million: str = ""
    output_price_per_million: str = ""
    price_currency: str = ""
    max_tokens: str = ""
    thinking: str = ""


def _resolve_client(agent_ref: str, config: Mapping[str, _RoleBinding]) -> LLMClient:
    """Internal: build the client for a role's binding; fail-closed (SPEC §4.2)."""
    binding = config.get(agent_ref)
    if binding is None:
        raise ProviderModelSelectionError(f"no provider/model binding for role '{agent_ref}'")
    if binding.provider == _PROVIDER_FAKE:
        return FakeLLMClient()
    if binding.provider == _PROVIDER_ANTHROPIC:
        if not binding.model:
            raise ProviderModelSelectionError(
                f"role '{agent_ref}' selects provider 'anthropic' without a model"
            )
        missing = [
            name
            for name, value in (
                ("input price", binding.input_price_per_million),
                ("output price", binding.output_price_per_million),
                ("price currency", binding.price_currency),
                ("max tokens", binding.max_tokens),
                ("thinking", binding.thinking),
            )
            if not value
        ]
        if missing:
            raise ProviderModelSelectionError(
                f"role '{agent_ref}' selects provider 'anthropic' without " + ", ".join(missing)
            )
        try:
            pricing = TokenPricing(
                input_per_million=Decimal(binding.input_price_per_million),
                output_per_million=Decimal(binding.output_price_per_million),
                currency=binding.price_currency,
            )
        except (InvalidOperation, ValueError) as exc:
            raise ProviderModelSelectionError(
                f"role '{agent_ref}' has invalid Anthropic pricing: {exc}"
            ) from exc
        try:
            return AnthropicLLMClient(
                model=binding.model,
                pricing=pricing,
                max_tokens=_parse_max_tokens(agent_ref, binding.max_tokens),
                thinking=_parse_thinking(agent_ref, binding.thinking),
            )
        except ValueError as exc:
            # The adapter also refuses a budget that is not below max_tokens — a pair only visible
            # once both values are known, and refused before a single token is bought (ADR-0052 §2).
            raise ProviderModelSelectionError(
                f"role '{agent_ref}' has an invalid Anthropic request budget: {exc}"
            ) from exc
    raise ProviderModelSelectionError(
        f"role '{agent_ref}' selects unknown provider '{binding.provider}'"
    )


def _parse_max_tokens(agent_ref: str, raw: str) -> int:
    """Internal: the role's output budget; fail-closed, never defaulted (`ADR-0052` §1).

    No value is chosen here when the configuration is unusable — a default would be a guess about
    the role's model, and there is no upper bound to check against for the same reason.
    """
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProviderModelSelectionError(
            f"role '{agent_ref}' has a non-integer max tokens value '{raw}'"
        ) from exc
    if value < 1:
        raise ProviderModelSelectionError(
            f"role '{agent_ref}' has a max tokens value below 1: {value}"
        )
    return value


def _parse_thinking(agent_ref: str, raw: str) -> ThinkingSetting:
    """Internal: the role's extended-thinking choice; the grammar itself is the value object's."""
    try:
        return ThinkingSetting.parse(raw)
    except ValueError as exc:
        raise ProviderModelSelectionError(f"role '{agent_ref}' has an {exc}") from exc


def _env_token(agent_ref: str) -> str:
    """Internal: normalize a role id to an env-safe token (impl detail, SPEC §1.2)."""
    return "".join(ch if ch.isalnum() else "_" for ch in agent_ref).upper()


def _load_config_from_env(
    environ: Mapping[str, str], roles: Iterable[str]
) -> dict[str, _RoleBinding]:
    """Internal: build ``role -> _RoleBinding`` from the environment for the given roles.

    Provider from ``OMEMO_PROVIDER__<token>``, model from ``OMEMO_MODEL__<token>``, the output
    budget from ``OMEMO_MAX_TOKENS__<token>`` and the thinking choice from
    ``OMEMO_THINKING__<token>`` (role normalized). Roles without a provider entry are omitted
    (fail-closed at resolution). Secrets are never read here (PROJECT §6).
    """
    config: dict[str, _RoleBinding] = {}
    for role in roles:
        token = _env_token(role)
        provider = environ.get(f"{_ENV_PROVIDER_PREFIX}{token}")
        if provider is None:
            continue
        model = environ.get(f"{_ENV_MODEL_PREFIX}{token}", "")
        input_price = environ.get(f"{_ENV_INPUT_PRICE_PREFIX}{token}", "")
        output_price = environ.get(f"{_ENV_OUTPUT_PRICE_PREFIX}{token}", "")
        currency = environ.get(f"{_ENV_PRICE_CURRENCY_PREFIX}{token}", "")
        max_tokens = environ.get(f"{_ENV_MAX_TOKENS_PREFIX}{token}", "")
        thinking = environ.get(f"{_ENV_THINKING_PREFIX}{token}", "")
        config[role] = _RoleBinding(
            provider=provider.strip().lower(),
            model=model.strip(),
            input_price_per_million=input_price.strip(),
            output_price_per_million=output_price.strip(),
            price_currency=currency.strip(),
            max_tokens=max_tokens.strip(),
            thinking=thinking.strip(),
        )
    return config
