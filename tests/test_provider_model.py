"""Provider/model selection ownership — gates (ADR-0016; PROVIDER_MODEL_ACCEPTANCE §1-§5).

The public contract (``role -> LLMClient``) is exercised through :func:`client_for_role`, with the
environment provided explicitly (DI, ACCEPTANCE §0). Anthropic construction uses a dummy key
(offline) purely to build the adapter — no model is invoked. One internal unit test covers env-token
normalization/omission (parsing logic worth testing directly).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from omemo_content_factory.infrastructure import provider_model
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.llm import AnthropicLLMClient
from omemo_content_factory.infrastructure.provider_model import (
    ProviderModelSelectionError,
    client_for_role,
)

_ROLE = "script_writer@v1"


def _env(
    provider: str | None = None, model: str | None = None, *, pricing: bool = True
) -> dict[str, str]:
    """Environment for role `_ROLE` (token SCRIPT_WRITER_V1)."""
    env: dict[str, str] = {}
    if provider is not None:
        env["OMEMO_PROVIDER__SCRIPT_WRITER_V1"] = provider
    if model is not None:
        env["OMEMO_MODEL__SCRIPT_WRITER_V1"] = model
    if provider == "anthropic" and pricing:
        env["OMEMO_INPUT_PRICE_PER_MILLION__SCRIPT_WRITER_V1"] = "3"
        env["OMEMO_OUTPUT_PRICE_PER_MILLION__SCRIPT_WRITER_V1"] = "15"
        env["OMEMO_PRICE_CURRENCY__SCRIPT_WRITER_V1"] = "USD"
    return env


# --- §1 Ownership: selection comes from config/env via the single public entry ------------


def test_ownership_anthropic_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # construction only, no call
    client = client_for_role(_ROLE, _env(provider="anthropic", model="claude-x"))
    assert isinstance(client, AnthropicLLMClient)
    assert client._model == "claude-x"  # model taken from config, not from any input
    assert client._pricing.input_per_million == Decimal("3")


def test_ownership_fake_from_env() -> None:
    assert isinstance(client_for_role(_ROLE, _env(provider="fake")), FakeLLMClient)


# --- §2 Config-driven swap: change only the environment -> different client ----------------


def test_swap_via_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    a = client_for_role(_ROLE, _env(provider="anthropic", model="m1"))
    b = client_for_role(_ROLE, _env(provider="anthropic", model="m2"))
    assert isinstance(a, AnthropicLLMClient) and isinstance(b, AnthropicLLMClient)
    assert a._model == "m1" and b._model == "m2"  # model swap via config alone
    assert isinstance(client_for_role(_ROLE, _env(provider="fake")), FakeLLMClient)


# --- §3 Keyless: fake provider yields a usable client without a key ------------------------


def test_keyless_fake_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = client_for_role(_ROLE, _env(provider="fake"))
    assert isinstance(client, FakeLLMClient)
    assert set(client.complete(system="s", user="u", fields=["title"]).fields) == {"title"}


# --- §4 Fail-closed: missing / invalid binding -> explicit error, no silent default -------


def test_fail_closed_missing_binding() -> None:
    with pytest.raises(ProviderModelSelectionError):
        client_for_role(_ROLE, {})


def test_fail_closed_anthropic_without_model() -> None:
    with pytest.raises(ProviderModelSelectionError):
        client_for_role(_ROLE, _env(provider="anthropic"))


def test_fail_closed_anthropic_without_complete_pricing() -> None:
    with pytest.raises(ProviderModelSelectionError, match="without input price"):
        client_for_role(_ROLE, _env(provider="anthropic", model="claude-x", pricing=False))


@pytest.mark.parametrize("rate", ["not-a-number", "-1", "NaN"])
def test_fail_closed_anthropic_with_invalid_pricing(rate: str) -> None:
    env = _env(provider="anthropic", model="claude-x")
    env["OMEMO_INPUT_PRICE_PER_MILLION__SCRIPT_WRITER_V1"] = rate
    with pytest.raises(ProviderModelSelectionError, match="invalid Anthropic pricing"):
        client_for_role(_ROLE, env)


def test_fail_closed_unknown_provider() -> None:
    with pytest.raises(ProviderModelSelectionError):
        client_for_role(_ROLE, _env(provider="openai", model="gpt"))


# --- internal unit (only where necessary): env parsing normalizes and omits ---------------


def test_internal_load_config_normalizes_and_omits() -> None:
    config = provider_model._load_config_from_env(
        {"OMEMO_PROVIDER__SCRIPT_WRITER_V1": "FAKE"}, [_ROLE, "unbound@v1"]
    )
    assert config[_ROLE].provider == "fake"  # normalized to lower
    assert "unbound@v1" not in config  # no env entry -> omitted (fail-closed at resolve)
