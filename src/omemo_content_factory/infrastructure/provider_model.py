"""Provider/model selection ownership — Content Factory owns which provider/model a role uses.

Realizes `ADR-0016` / `PROVIDER_MODEL_SPEC`. The **single public contract** is
:func:`client_for_role` — ``role + environment -> ready LLMClient`` (SPEC §4.1) — behind the
existing port (`ADR-0014`). Keyless is the **fake** provider; a missing or invalid binding **fails
closed** — never a silent hardcoded model default (PROJECT §5, §10). Reuses ``AnthropicLLMClient`` /
``FakeLLMClient``; the Composition Root still *receives* the resolved client and gains no selection
logic. ADR-0029's measured port result does not change selection ownership.

The ``role -> (provider, model, pricing, max_tokens, thinking)`` binding, its env format, and the
two-step load/resolve are **implementation details** (SPEC §1.2): provider granularity is per-role,
values come from the environment. Real-provider pricing is explicit and fail-closed (`ADR-0029`); so
are the output budget and the extended-thinking choice (`ADR-0052`). No rate, budget or thinking
default is hardcoded — each would be a guess about the role's model.

Providers (ADR-0094): ``anthropic`` (its SDK reads its own key), ``fake``, and the OpenAI-dialect
family — a **preset** (``xai``, ``openai``, ``openrouter``, …) or ``openai-compatible`` with an
explicit base URL. The family's key is read **by the name** the preset or ``OMEMO_API_KEY_ENV__*``
gives, from the environment this function was handed, and is passed to the client, which never
prints it. Every per-role variable may fall back to an operator-written ``__DEFAULT`` one.
Callers depend only on :func:`client_for_role` and :class:`ProviderModelSelectionError`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TypeVar

from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.llm import (
    AnthropicLLMClient,
    LLMClient,
    ThinkingSetting,
    TokenPricing,
)
from omemo_content_factory.infrastructure.openai_compatible_llm import (
    EFFORT_LEVELS,
    OpenAICompatibleLLMClient,
    Structured,
    Wire,
)

_E = TypeVar("_E", bound=Enum)

__all__ = ["PROVIDER_PRESETS", "ProviderModelSelectionError", "client_for_role"]

_PROVIDER_ANTHROPIC = "anthropic"
_PROVIDER_FAKE = "fake"
_ENV_PROVIDER_PREFIX = "OMEMO_PROVIDER__"
_ENV_MODEL_PREFIX = "OMEMO_MODEL__"
_ENV_INPUT_PRICE_PREFIX = "OMEMO_INPUT_PRICE_PER_MILLION__"
_ENV_OUTPUT_PRICE_PREFIX = "OMEMO_OUTPUT_PRICE_PER_MILLION__"
_ENV_PRICE_CURRENCY_PREFIX = "OMEMO_PRICE_CURRENCY__"
_ENV_MAX_TOKENS_PREFIX = "OMEMO_MAX_TOKENS__"
_ENV_THINKING_PREFIX = "OMEMO_THINKING__"
_ENV_BASE_URL_PREFIX = "OMEMO_BASE_URL__"
_ENV_API_KEY_ENV_PREFIX = "OMEMO_API_KEY_ENV__"
_ENV_WIRE_PREFIX = "OMEMO_WIRE__"
_ENV_STRUCTURED_PREFIX = "OMEMO_STRUCTURED__"
_ENV_TOKEN_PARAM_PREFIX = "OMEMO_TOKEN_PARAM__"
_DEFAULT_TOKEN = "DEFAULT"
_PROVIDER_CUSTOM = "openai-compatible"


@dataclass(frozen=True, slots=True)
class _Preset:
    """What a named OpenAI-dialect provider contributes: where it lives and how it is keyed.

    A convenience, not an authority (ADR-0094 §4): written from vendor documentation, never
    exercised here with a real key. Every field can be overridden in the environment.
    """

    base_url: str
    key_env: str | None
    token_param: str = "max_tokens"


PROVIDER_PRESETS: Mapping[str, _Preset] = {
    "xai": _Preset("https://api.x.ai/v1", "XAI_API_KEY"),
    "openai": _Preset("https://api.openai.com/v1", "OPENAI_API_KEY", "max_completion_tokens"),
    "openrouter": _Preset("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": _Preset("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "mistral": _Preset("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "deepseek": _Preset("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "together": _Preset("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "ollama": _Preset("http://localhost:11434/v1", None),
    "lmstudio": _Preset("http://localhost:1234/v1", None),
}


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
    return _resolve_client(agent_ref, config, environ)


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
    base_url: str = ""
    api_key_env: str = ""
    wire: str = ""
    structured: str = ""
    token_param: str = ""


def _resolve_client(
    agent_ref: str, config: Mapping[str, _RoleBinding], environ: Mapping[str, str]
) -> LLMClient:
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
    if binding.provider == _PROVIDER_CUSTOM or binding.provider in PROVIDER_PRESETS:
        return _resolve_compatible(agent_ref, binding, environ)
    known = ", ".join(["anthropic", "fake", _PROVIDER_CUSTOM, *sorted(PROVIDER_PRESETS)])
    raise ProviderModelSelectionError(
        f"role '{agent_ref}' selects unknown provider '{binding.provider}' (known: {known})"
    )


def _resolve_compatible(
    agent_ref: str, binding: _RoleBinding, environ: Mapping[str, str]
) -> LLMClient:
    """Internal: the OpenAI-dialect family (ADR-0094); every shortfall is named, never guessed."""
    provider = binding.provider
    preset = PROVIDER_PRESETS.get(provider)
    base_url = binding.base_url or (preset.base_url if preset else "")
    missing = [
        name
        for name, value in (
            ("a model", binding.model),
            ("a base URL", base_url),
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
            f"role '{agent_ref}' selects provider '{provider}' without " + ", ".join(missing)
        )
    try:
        pricing = TokenPricing(
            input_per_million=Decimal(binding.input_price_per_million),
            output_per_million=Decimal(binding.output_price_per_million),
            currency=binding.price_currency,
        )
    except (InvalidOperation, ValueError) as exc:
        raise ProviderModelSelectionError(
            f"role '{agent_ref}' has invalid {provider} pricing: {exc}"
        ) from exc

    key_env = binding.api_key_env or (preset.key_env if preset else None)
    api_key: str | None = None
    if key_env:
        api_key = environ.get(key_env, "").strip()
        if not api_key:
            raise ProviderModelSelectionError(
                f"role '{agent_ref}' selects provider '{provider}' but the environment variable "
                f"{key_env} (its API key) is not set"
            )
    try:
        return OpenAICompatibleLLMClient(
            model=binding.model,
            pricing=pricing,
            max_tokens=_parse_max_tokens(agent_ref, binding.max_tokens),
            base_url=base_url,
            provider=provider,
            api_key=api_key,
            wire=_parse_enum(agent_ref, "wire", binding.wire, Wire, Wire.CHAT),
            structured=_parse_enum(
                agent_ref, "structured", binding.structured, Structured, Structured.TOOL
            ),
            effort=_parse_effort(agent_ref, provider, binding.thinking),
            token_param=binding.token_param or (preset.token_param if preset else "max_tokens"),
        )
    except ValueError as exc:
        raise ProviderModelSelectionError(
            f"role '{agent_ref}' has an invalid {provider} setting: {exc}"
        ) from exc


def _parse_enum(agent_ref: str, name: str, raw: str, enum_type: type[_E], default: _E) -> _E:
    """Internal: a closed word from the environment; unknown is an error, never the default."""
    if not raw:
        return default
    try:
        return enum_type(raw.lower())
    except ValueError:
        allowed = ", ".join(str(member.value) for member in enum_type)
        raise ProviderModelSelectionError(
            f"role '{agent_ref}' has an unknown {name} '{raw}' (expected {allowed})"
        ) from None


def _parse_effort(agent_ref: str, provider: str, raw: str) -> str | None:
    """Internal: this family's thinking grammar, ``inherit`` or ``effort:<level>`` (ADR-0094 §6)."""
    value = raw.strip().lower()
    if value in ("inherit", "disabled"):
        return None
    if value.startswith("effort:"):
        level = value.removeprefix("effort:").strip()
        if level in EFFORT_LEVELS:
            return level
    raise ProviderModelSelectionError(
        f"role '{agent_ref}' has thinking '{raw}', which provider '{provider}' does not take "
        "(adaptive and budget:<N> are Anthropic's; use inherit or "
        f"effort:{'|'.join(EFFORT_LEVELS)})"
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

    Every value comes from ``OMEMO_<NAME>__<token>`` (role normalized) and, when that is absent or
    blank, from the operator-written ``OMEMO_<NAME>__DEFAULT`` (ADR-0094 §5). A role with no
    provider by either route is omitted (fail-closed at resolution). The environment is only
    *read* for configuration here; a provider key is looked up later, by name (PROJECT §6).
    """

    def value(prefix: str, token: str) -> str:
        own = environ.get(f"{prefix}{token}", "").strip()
        return own or environ.get(f"{prefix}{_DEFAULT_TOKEN}", "").strip()

    config: dict[str, _RoleBinding] = {}
    for role in roles:
        token = _env_token(role)
        provider = value(_ENV_PROVIDER_PREFIX, token)
        if not provider:
            continue
        config[role] = _RoleBinding(
            provider=provider.lower(),
            model=value(_ENV_MODEL_PREFIX, token),
            input_price_per_million=value(_ENV_INPUT_PRICE_PREFIX, token),
            output_price_per_million=value(_ENV_OUTPUT_PRICE_PREFIX, token),
            price_currency=value(_ENV_PRICE_CURRENCY_PREFIX, token),
            max_tokens=value(_ENV_MAX_TOKENS_PREFIX, token),
            thinking=value(_ENV_THINKING_PREFIX, token),
            base_url=value(_ENV_BASE_URL_PREFIX, token),
            api_key_env=value(_ENV_API_KEY_ENV_PREFIX, token),
            wire=value(_ENV_WIRE_PREFIX, token),
            structured=value(_ENV_STRUCTURED_PREFIX, token),
            token_param=value(_ENV_TOKEN_PARAM_PREFIX, token),
        )
    return config
