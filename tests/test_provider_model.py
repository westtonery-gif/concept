"""Provider/model selection ownership — gates (ADR-0016; PROVIDER_MODEL_ACCEPTANCE §1-§5).

The public contract (``role -> LLMClient``) is exercised through :func:`client_for_role`, with the
environment provided explicitly (DI, ACCEPTANCE §0). Anthropic construction uses a dummy key
(offline) purely to build the adapter — no model is invoked. One internal unit test covers env-token
normalization/omission (parsing logic worth testing directly).
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import anthropic
import pytest
from anthropic._utils import maybe_transform
from anthropic.types import Message, Usage, message_create_params
from anthropic.types.tool_use_block import ToolUseBlock

from omemo_content_factory.infrastructure import provider_model
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.llm import (
    AnthropicLLMClient,
    ThinkingMode,
    ThinkingSetting,
    TokenPricing,
)
from omemo_content_factory.infrastructure.provider_model import (
    ProviderModelSelectionError,
    client_for_role,
)
from omemo_content_factory.tools.toolbox import Toolbox

_ROLE = "script_writer@v1"


def _env(
    provider: str | None = None,
    model: str | None = None,
    *,
    pricing: bool = True,
    budget: bool = True,
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
    if provider == "anthropic" and budget:
        env["OMEMO_MAX_TOKENS__SCRIPT_WRITER_V1"] = "16000"
        env["OMEMO_THINKING__SCRIPT_WRITER_V1"] = "adaptive"
    return env


# --- §1 Ownership: selection comes from config/env via the single public entry ------------


def test_ownership_anthropic_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # construction only, no call
    client = client_for_role(_ROLE, _env(provider="anthropic", model="claude-x"))
    assert isinstance(client, AnthropicLLMClient)
    assert client._model == "claude-x"  # model taken from config, not from any input
    assert client._pricing.input_per_million == Decimal("3")
    # ADR-0052: the output budget and the thinking choice come from the same binding.
    assert client._max_tokens == 16000
    assert client._thinking == ThinkingSetting(ThinkingMode.ADAPTIVE)


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


# --- §4/§7 The request budget: fail-closed, no default, and what reaches the provider ------
# PROVIDER_MODEL_ACCEPTANCE 1.2 §4 ("вариант предела ответа и thinking") and §7 (`ADR-0052`).
# §7 B — both values on every turn of a Tool loop — is `LTL-09` in tests/test_llm_tool_loop.py.


def test_fail_closed_anthropic_without_budget_or_thinking() -> None:
    """§4: both are required exactly like the prices; the old 2048 is never substituted."""
    with pytest.raises(ProviderModelSelectionError, match="without max tokens, thinking"):
        client_for_role(_ROLE, _env(provider="anthropic", model="claude-x", budget=False))


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "lots", "16 000", " "])
def test_fail_closed_anthropic_with_invalid_max_tokens(value: str) -> None:
    env = _env(provider="anthropic", model="claude-x")
    env["OMEMO_MAX_TOKENS__SCRIPT_WRITER_V1"] = value
    with pytest.raises(ProviderModelSelectionError, match="max tokens"):
        client_for_role(_ROLE, env)


@pytest.mark.parametrize("value", ["enabled", "on", "off", "true", "budget:", "budget:lots", "-"])
def test_fail_closed_anthropic_with_unknown_thinking_value(value: str) -> None:
    """An unrecognised word is refused, never degraded into inheriting the model's default."""
    env = _env(provider="anthropic", model="claude-x")
    env["OMEMO_THINKING__SCRIPT_WRITER_V1"] = value
    with pytest.raises(ProviderModelSelectionError, match="thinking"):
        client_for_role(_ROLE, env)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("adaptive", ThinkingSetting(ThinkingMode.ADAPTIVE)),
        ("DISABLED", ThinkingSetting(ThinkingMode.DISABLED)),
        ("  Inherit  ", ThinkingSetting(ThinkingMode.INHERIT)),
        ("budget:4000", ThinkingSetting(ThinkingMode.BUDGET, 4000)),
    ],
)
def test_thinking_grammar_is_closed_and_case_insensitive(
    value: str, expected: ThinkingSetting, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    env = _env(provider="anthropic", model="claude-x")
    env["OMEMO_THINKING__SCRIPT_WRITER_V1"] = value
    client = client_for_role(_ROLE, env)
    assert isinstance(client, AnthropicLLMClient)
    assert client._thinking == expected


@pytest.mark.parametrize(
    ("budget", "max_tokens", "reason"),
    [("budget:1023", "16000", "1024"), ("budget:16000", "16000", "below max_tokens")],
)
def test_fail_closed_thinking_budget_inconsistent_with_max_tokens(
    budget: str, max_tokens: str, reason: str
) -> None:
    """§7 C: a pair the provider would reject with a 400 costs zero tokens here."""
    env = _env(provider="anthropic", model="claude-x")
    env["OMEMO_THINKING__SCRIPT_WRITER_V1"] = budget
    env["OMEMO_MAX_TOKENS__SCRIPT_WRITER_V1"] = max_tokens
    with pytest.raises(ProviderModelSelectionError, match=reason):
        client_for_role(_ROLE, env)


def test_fake_needs_neither_budget_nor_thinking() -> None:
    assert isinstance(client_for_role(_ROLE, _env(provider="fake", budget=False)), FakeLLMClient)


def test_adapter_cannot_be_built_without_a_budget_decision() -> None:
    """§7 D: the defect's cause — a constructor default — is gone, so nobody can inherit it."""
    pricing = TokenPricing(Decimal("3"), Decimal("15"), "USD")
    with pytest.raises(TypeError, match="max_tokens"):
        AnthropicLLMClient(model="claude-x", pricing=pricing)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="thinking"):
        AnthropicLLMClient(model="claude-x", pricing=pricing, max_tokens=16000)  # type: ignore[call-arg]


class _Recorder:
    """The provider endpoint, recording each request and answering the forced structured call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> Message:
        self.calls.append(copy.deepcopy(kwargs))
        return Message(
            id="msg_test",
            content=[
                ToolUseBlock(id="f1", input={"draft": "x"}, name="emit_fields", type="tool_use")
            ],
            model="claude-x",
            role="assistant",
            stop_reason="tool_use",
            stop_sequence=None,
            type="message",
            usage=Usage(input_tokens=1, output_tokens=1),
        )


class _RecordingAnthropic:
    def __init__(self, recorder: _Recorder) -> None:
        self.messages = recorder


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        (ThinkingSetting(ThinkingMode.ADAPTIVE), {"type": "adaptive"}),
        (ThinkingSetting(ThinkingMode.DISABLED), {"type": "disabled"}),
        (ThinkingSetting(ThinkingMode.BUDGET, 4000), {"type": "enabled", "budget_tokens": 4000}),
    ],
)
def test_request_carries_the_configured_budget_and_thinking(
    setting: ThinkingSetting, expected: object
) -> None:
    """§7 A: what the binding holds is what the provider is asked, with nothing substituted."""
    recorder = _recorded_call(setting)
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["max_tokens"] == 16000
    assert recorder.calls[0]["thinking"] == expected


def test_inherit_sends_no_thinking_field_at_all() -> None:
    """§7 A: the SDK strips an ``omit`` value, so the body is one that never mentioned thinking."""
    recorder = _recorded_call(ThinkingSetting(ThinkingMode.INHERIT))
    assert isinstance(recorder.calls[0]["thinking"], anthropic.Omit)
    body = cast(
        dict[str, object],
        maybe_transform(
            {"model": "claude-x", "max_tokens": 16000, "messages": [], "thinking": anthropic.omit},
            message_create_params.MessageCreateParamsNonStreaming,
        ),
    )
    assert "thinking" not in body


def _recorded_call(setting: ThinkingSetting) -> _Recorder:
    recorder = _Recorder()
    client = AnthropicLLMClient(
        model="claude-x",
        pricing=TokenPricing(Decimal("3"), Decimal("15"), "USD"),
        max_tokens=16000,
        thinking=setting,
        client=cast(anthropic.Anthropic, _RecordingAnthropic(recorder)),
        clock=lambda: datetime(2026, 9, 19, 12, tzinfo=UTC),
    )
    client.complete(
        system="s", user="u", fields=("draft",), toolbox=Toolbox(grants=(), available=())
    )
    return recorder
