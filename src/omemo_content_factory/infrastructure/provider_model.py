"""Provider/model selection ownership — Content Factory owns which provider/model a role uses.

Realizes `ADR-0016` / `PROVIDER_MODEL_SPEC`. The **single public contract** is
:func:`client_for_role` — ``role + environment -> ready LLMClient`` (SPEC §4.1) — behind the
existing port (`ADR-0014`). Keyless is the **fake** provider; a missing or invalid binding **fails
closed** — never a silent hardcoded model default (PROJECT §5, §10). Reuses ``AnthropicLLMClient`` /
``FakeLLMClient``; the Composition Root still *receives* the resolved client and gains no selection
logic. ADR-0029's measured port result does not change selection ownership.

The ``role -> (provider, model, pricing)`` binding, its env format, and the two-step load/resolve
are **implementation details** (SPEC §1.2): provider granularity is per-role, values come from the
environment, and API keys are never read here (the adapter's SDK obtains them; PROJECT §5, §6).
Real-provider pricing is explicit and fail-closed (`ADR-0029`); no rate is hardcoded.
Callers depend only on :func:`client_for_role` and :class:`ProviderModelSelectionError`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.llm import AnthropicLLMClient, LLMClient, TokenPricing

__all__ = ["ProviderModelSelectionError", "client_for_role"]

_PROVIDER_ANTHROPIC = "anthropic"
_PROVIDER_FAKE = "fake"
_ENV_PROVIDER_PREFIX = "OMEMO_PROVIDER__"
_ENV_MODEL_PREFIX = "OMEMO_MODEL__"
_ENV_INPUT_PRICE_PREFIX = "OMEMO_INPUT_PRICE_PER_MILLION__"
_ENV_OUTPUT_PRICE_PREFIX = "OMEMO_OUTPUT_PRICE_PER_MILLION__"
_ENV_PRICE_CURRENCY_PREFIX = "OMEMO_PRICE_CURRENCY__"


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
    """Internal: provider/model and explicit pricing selected for one role (ADR-0029 §2)."""

    provider: str
    model: str = ""
    input_price_per_million: str = ""
    output_price_per_million: str = ""
    price_currency: str = ""


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
        return AnthropicLLMClient(model=binding.model, pricing=pricing)
    raise ProviderModelSelectionError(
        f"role '{agent_ref}' selects unknown provider '{binding.provider}'"
    )


def _env_token(agent_ref: str) -> str:
    """Internal: normalize a role id to an env-safe token (impl detail, SPEC §1.2)."""
    return "".join(ch if ch.isalnum() else "_" for ch in agent_ref).upper()


def _load_config_from_env(
    environ: Mapping[str, str], roles: Iterable[str]
) -> dict[str, _RoleBinding]:
    """Internal: build ``role -> _RoleBinding`` from the environment for the given roles.

    Provider from ``OMEMO_PROVIDER__<token>``, model from ``OMEMO_MODEL__<token>`` (role
    normalized). Roles without a provider entry are omitted (fail-closed at resolution). Secrets are
    never read here (PROJECT §6).
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
        config[role] = _RoleBinding(
            provider=provider.strip().lower(),
            model=model.strip(),
            input_price_per_million=input_price.strip(),
            output_price_per_million=output_price.strip(),
            price_currency=currency.strip(),
        )
    return config
