"""Any model provider through one OpenAI-compatible client (ADR-0094): ``OAI`` and ``PRV`` rows.

The client talks real HTTP to a local server that plays the provider — the way every vendor adapter
here is tested — in both dialects (``chat`` and ``responses``). No provider's real answer is seen.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from omemo_content_factory.infrastructure.llm import LLMError, LLMTaskExecutor, TokenPricing
from omemo_content_factory.infrastructure.openai_compatible_llm import (
    OpenAICompatibleLLMClient,
    Structured,
    Wire,
)
from omemo_content_factory.infrastructure.provider_model import (
    PROVIDER_PRESETS,
    ProviderModelSelectionError,
    client_for_role,
)
from omemo_content_factory.tools.contract import Tool
from omemo_content_factory.tools.text_metrics import TextMetrics
from omemo_content_factory.tools.toolbox import Toolbox

_PRICING = TokenPricing(Decimal("2"), Decimal("10"), "USD")
_KEY = "sk-super-secret-key-9f3a"


class _Provider:
    """A local server that answers scripted ``(status, body)`` pairs and records every request."""

    def __init__(self) -> None:
        self.script: list[tuple[int, Any]] = []
        self.requests: list[dict[str, Any]] = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                provider.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "json": json.loads(raw) if raw else None,
                    }
                )
                status, body = provider.script.pop(0) if provider.script else (500, "unscripted")
                payload = body if isinstance(body, str) else json.dumps(body)
                data = payload.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"
        threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True
        ).start()

    def queue(self, *items: tuple[int, Any]) -> None:
        self.script.extend(items)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def provider() -> Iterator[_Provider]:
    server = _Provider()
    yield server
    server.stop()


class _Ticking:
    """A clock that advances 0.5 s per read, so every call has a real, deterministic duration."""

    def __init__(self) -> None:
        self._now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(milliseconds=500)
        return self._now


def _client(provider: _Provider, **overrides: Any) -> OpenAICompatibleLLMClient:
    settings: dict[str, Any] = {
        "model": "grok-test",
        "pricing": _PRICING,
        "max_tokens": 500,
        "base_url": provider.url,
        "provider": "xai",
        "api_key": _KEY,
        "clock": _Ticking(),
        "sleep": lambda seconds: None,
    }
    settings.update(overrides)
    return OpenAICompatibleLLMClient(**settings)


def _empty() -> Toolbox:
    return Toolbox(grants=(), available=())


def _metrics_toolbox() -> Toolbox:
    tool: Tool = TextMetrics()
    return Toolbox(grants=(tool.descriptor.ref,), available=(tool,))


# --- chat-dialect answers -----------------------------------------------------------------------


def _usage(prompt: int = 10, completion: int = 5) -> dict[str, int]:
    return {"prompt_tokens": prompt, "completion_tokens": completion}


def _chat_calls(*calls: tuple[str, dict[str, Any]], usage: dict[str, int] | None = None) -> Any:
    return (
        200,
        {
            "model": "grok-test-2026",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call_{index}",
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                            for index, (name, arguments) in enumerate(calls, start=1)
                        ],
                    }
                }
            ],
            "usage": usage or _usage(),
        },
    )


def _chat_text(text: str, usage: dict[str, int] | None = None) -> Any:
    return (
        200,
        {
            "model": "grok-test-2026",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": usage or _usage(),
        },
    )


# --- OAI ------------------------------------------------------------------------------------------


def test_oai_01_one_forced_call_returns_the_fields_and_carries_the_request(
    provider: _Provider,
) -> None:
    provider.queue(_chat_calls(("emit_fields", {"title": "T", "body": "B"})))
    completion = _client(provider).complete(
        system="sys", user="usr", fields=("title", "body"), toolbox=_empty()
    )
    assert dict(completion.fields) == {"title": "T", "body": "B"}
    (request,) = provider.requests
    sent = request["json"]
    assert request["path"] == "/v1/chat/completions"
    assert request["auth"] == f"Bearer {_KEY}"
    assert sent["model"] == "grok-test"
    assert sent["max_tokens"] == 500
    assert sent["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert [t["function"]["name"] for t in sent["tools"]] == ["emit_fields"]
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "emit_fields"}}
    assert "reasoning_effort" not in sent


def test_oai_01_the_token_field_and_reasoning_effort_follow_the_configuration(
    provider: _Provider,
) -> None:
    provider.queue(_chat_calls(("emit_fields", {"a": "1"})))
    _client(provider, token_param="max_completion_tokens", effort="high").complete(
        system="s", user="u", fields=("a",), toolbox=_empty()
    )
    sent = provider.requests[0]["json"]
    assert sent["max_completion_tokens"] == 500 and "max_tokens" not in sent
    assert sent["reasoning_effort"] == "high"


def test_oai_01_tool_required_asks_for_any_call_instead_of_naming_one(provider: _Provider) -> None:
    provider.queue(_chat_calls(("emit_fields", {"a": "1"})))
    _client(provider, structured=Structured.TOOL_REQUIRED).complete(
        system="s", user="u", fields=("a",), toolbox=_empty()
    )
    assert provider.requests[0]["json"]["tool_choice"] == "required"


def test_oai_01_a_provider_with_no_key_gets_no_authorization_header(provider: _Provider) -> None:
    provider.queue(_chat_calls(("emit_fields", {"a": "1"})))
    _client(provider, api_key=None).complete(system="s", user="u", fields=("a",), toolbox=_empty())
    assert provider.requests[0]["auth"] is None


def test_oai_01_an_answer_without_the_function_is_empty_fields_not_a_verdict(
    provider: _Provider,
) -> None:
    provider.queue(_chat_text("I refuse"))
    completion = _client(provider).complete(system="s", user="u", fields=("a",), toolbox=_empty())
    assert dict(completion.fields) == {}


def test_oai_01_a_nested_value_keeps_its_json_form(provider: _Provider) -> None:
    provider.queue(_chat_calls(("emit_fields", {"flags": ["x", "y"], "n": 3})))
    completion = _client(provider).complete(
        system="s", user="u", fields=("flags", "n"), toolbox=_empty()
    )
    assert completion.fields["flags"] == '["x", "y"]'
    assert completion.fields["n"] == "3"


def test_oai_02_a_granted_tool_runs_through_the_toolbox_and_its_result_is_fed_back(
    provider: _Provider,
) -> None:
    provider.queue(
        _chat_calls(("text_metrics", {"text": "hello world"})),
        _chat_calls(("emit_fields", {"answer": "done"})),
    )
    completion = _client(provider).complete(
        system="s", user="u", fields=("answer",), toolbox=_metrics_toolbox()
    )
    assert dict(completion.fields) == {"answer": "done"}
    first, second = (r["json"] for r in provider.requests)
    assert {t["function"]["name"] for t in first["tools"]} == {"text_metrics", "emit_fields"}
    assert first["tool_choice"] == second["tool_choice"] == "required"
    history = second["messages"]
    assert history[-2]["role"] == "assistant" and history[-2]["tool_calls"]
    assert history[-1]["role"] == "tool" and history[-1]["tool_call_id"] == "call_1"
    result = json.loads(history[-1]["content"])
    assert result["status"] == "ok" and result["data"]["characters"] == 11


def test_oai_03_json_mode_asks_for_an_object_and_decodes_it(provider: _Provider) -> None:
    provider.queue(_chat_text('```json\n{"a": "x", "b": "y"}\n```'))
    completion = _client(provider, structured=Structured.JSON).complete(
        system="Be brief.", user="u", fields=("a", "b"), toolbox=_empty()
    )
    assert dict(completion.fields) == {"a": "x", "b": "y"}
    sent = provider.requests[0]["json"]
    assert sent["response_format"] == {"type": "json_object"}
    assert "tools" not in sent
    assert sent["messages"][0]["content"].startswith("Be brief.")
    assert "exactly: a, b" in sent["messages"][0]["content"]


def test_oai_03_json_mode_with_a_tool_loops_until_the_model_answers_in_text(
    provider: _Provider,
) -> None:
    provider.queue(
        _chat_calls(("text_metrics", {"text": "abc"})),
        _chat_text('{"answer": "3 characters"}'),
    )
    completion = _client(provider, structured=Structured.JSON).complete(
        system="s", user="u", fields=("answer",), toolbox=_metrics_toolbox()
    )
    assert dict(completion.fields) == {"answer": "3 characters"}
    first, second = (r["json"] for r in provider.requests)
    assert first["tool_choice"] == second["tool_choice"] == "auto"
    assert [t["function"]["name"] for t in first["tools"]] == ["text_metrics"]


def test_oai_03_json_mode_garbage_is_empty_fields(provider: _Provider) -> None:
    provider.queue(_chat_text("sorry, no"))
    completion = _client(provider, structured=Structured.JSON).complete(
        system="s", user="u", fields=("a",), toolbox=_empty()
    )
    assert dict(completion.fields) == {}


def test_oai_04_every_turn_is_measured_and_priced_from_the_explicit_rates(
    provider: _Provider,
) -> None:
    provider.queue(
        _chat_calls(("text_metrics", {"text": "x"}), usage=_usage(1000, 500)),
        _chat_calls(("emit_fields", {"a": "1"}), usage=_usage(2000, 100)),
    )
    completion = _client(provider).complete(
        system="s", user="u", fields=("a",), toolbox=_metrics_toolbox()
    )
    first, second = completion.metrics
    assert first.provider == "xai" and first.model == "grok-test-2026"
    assert (first.input_tokens, first.output_tokens) == (1000, 500)
    assert first.cost_amount == Decimal("0.007") and first.cost_currency == "USD"
    assert second.cost_amount == Decimal("0.005")
    assert first.finished_at - first.started_at == timedelta(milliseconds=500)


def test_oai_04_a_failure_on_turn_two_keeps_turn_ones_metrics(provider: _Provider) -> None:
    provider.queue(
        _chat_calls(("text_metrics", {"text": "x"}), usage=_usage(1000, 500)),
        (401, {"error": {"message": "bad key"}}),
    )
    with pytest.raises(LLMError) as raised:
        _client(provider).complete(system="s", user="u", fields=("a",), toolbox=_metrics_toolbox())
    assert len(raised.value.metrics) == 1
    assert raised.value.metrics[0].cost_amount == Decimal("0.007")


def test_oai_04_a_missing_usage_block_is_zero_tokens_not_a_crash(provider: _Provider) -> None:
    provider.queue(
        (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c",
                                    "type": "function",
                                    "function": {"name": "emit_fields", "arguments": '{"a":"1"}'},
                                }
                            ]
                        }
                    }
                ]
            },
        )
    )
    (metric,) = (
        _client(provider).complete(system="s", user="u", fields=("a",), toolbox=_empty()).metrics
    )
    assert (metric.input_tokens, metric.output_tokens) == (0, 0)
    assert metric.model == "grok-test"  # the configured name when the answer gives none


def test_oai_05_failures_are_llm_errors_and_the_key_is_never_in_one(provider: _Provider) -> None:
    client = _client(provider)
    assert _KEY not in repr(client)

    provider.queue((401, {"error": {"message": "Incorrect API key provided"}}))
    with pytest.raises(LLMError, match="HTTP 401: Incorrect API key") as raised:
        client.complete(system="s", user="u", fields=("a",), toolbox=_empty())
    assert _KEY not in str(raised.value)

    provider.queue((200, "this is not json"))
    with pytest.raises(LLMError, match="not JSON"):
        client.complete(system="s", user="u", fields=("a",), toolbox=_empty())

    provider.queue((200, {"choices": []}))
    with pytest.raises(LLMError, match="no choices"):
        client.complete(system="s", user="u", fields=("a",), toolbox=_empty())


def test_oai_05_an_unreachable_server_is_an_llm_error_after_the_retries() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    sleeps: list[float] = []
    client = OpenAICompatibleLLMClient(
        model="m",
        pricing=_PRICING,
        max_tokens=10,
        base_url=f"http://127.0.0.1:{port}/v1",
        sleep=sleeps.append,
    )
    with pytest.raises(LLMError, match="could not be reached"):
        client.complete(system="s", user="u", fields=("a",), toolbox=_empty())
    assert sleeps == [1.0, 3.0]


def test_oai_05_a_final_answer_mixed_with_another_call_is_refused(provider: _Provider) -> None:
    provider.queue(
        _chat_calls(("emit_fields", {"a": "1"}), ("text_metrics", {"text": "x"})),
    )
    with pytest.raises(LLMError, match="mixed"):
        _client(provider).complete(system="s", user="u", fields=("a",), toolbox=_metrics_toolbox())


def test_oai_05_unparseable_tool_arguments_are_an_llm_error(provider: _Provider) -> None:
    provider.queue(
        (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c",
                                    "type": "function",
                                    "function": {"name": "text_metrics", "arguments": "{oops"},
                                }
                            ]
                        }
                    }
                ]
            },
        )
    )
    with pytest.raises(LLMError, match="unparseable arguments"):
        _client(provider).complete(system="s", user="u", fields=("a",), toolbox=_metrics_toolbox())


def test_oai_05_the_reserved_function_name_cannot_be_granted() -> None:
    class Rogue:
        descriptor = TextMetrics.descriptor.__class__(
            tool_id="emit_fields",
            version=TextMetrics.descriptor.version,
            description="x",
            parameters=(),
        )

        def invoke(self, arguments: Any, /) -> dict[str, Any]:
            return {}

    rogue: Tool = Rogue()
    box = Toolbox(grants=(rogue.descriptor.ref,), available=(rogue,))
    client = OpenAICompatibleLLMClient(
        model="m", pricing=_PRICING, max_tokens=10, base_url="http://127.0.0.1:9/v1"
    )
    with pytest.raises(LLMError, match="reserved"):
        client.complete(system="s", user="u", fields=("a",), toolbox=box)


def test_oai_06_a_transient_error_is_retried_and_then_succeeds(provider: _Provider) -> None:
    sleeps: list[float] = []
    provider.queue(
        (429, {"error": {"message": "slow down"}}),
        _chat_calls(("emit_fields", {"a": "1"})),
    )
    completion = _client(provider, sleep=sleeps.append).complete(
        system="s", user="u", fields=("a",), toolbox=_empty()
    )
    assert dict(completion.fields) == {"a": "1"}
    assert len(provider.requests) == 2 and sleeps == [1.0]
    assert len(completion.metrics) == 1  # the failed attempt returned no usage


def test_oai_06_persistent_503_stops_after_three_attempts(provider: _Provider) -> None:
    sleeps: list[float] = []
    provider.queue(*[(503, {"error": "down"})] * 3)
    with pytest.raises(LLMError, match="HTTP 503"):
        _client(provider, sleep=sleeps.append).complete(
            system="s", user="u", fields=("a",), toolbox=_empty()
        )
    assert len(provider.requests) == 3 and sleeps == [1.0, 3.0]


def test_oai_06_a_401_is_not_retried(provider: _Provider) -> None:
    sleeps: list[float] = []
    provider.queue((401, {"error": "no"}))
    with pytest.raises(LLMError):
        _client(provider, sleep=sleeps.append).complete(
            system="s", user="u", fields=("a",), toolbox=_empty()
        )
    assert len(provider.requests) == 1 and sleeps == []


def _resp_calls(*calls: tuple[str, dict[str, Any]], usage: tuple[int, int] = (10, 5)) -> Any:
    return (
        200,
        {
            "model": "grok-test-2026",
            "output": [
                {
                    "type": "function_call",
                    "call_id": f"call_{index}",
                    "name": name,
                    "arguments": json.dumps(arguments),
                }
                for index, (name, arguments) in enumerate(calls, start=1)
            ],
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
        },
    )


def test_oai_07_the_responses_dialect_gives_the_same_fields_and_the_same_loop(
    provider: _Provider,
) -> None:
    provider.queue(
        _resp_calls(("text_metrics", {"text": "hello world"}), usage=(1000, 500)),
        _resp_calls(("emit_fields", {"answer": "done"})),
    )
    completion = _client(provider, wire=Wire.RESPONSES, effort="low").complete(
        system="sys", user="usr", fields=("answer",), toolbox=_metrics_toolbox()
    )
    assert dict(completion.fields) == {"answer": "done"}
    first, second = provider.requests
    assert first["path"] == "/v1/responses"
    sent = first["json"]
    assert sent["instructions"] == "sys" and sent["max_output_tokens"] == 500
    assert sent["input"] == [{"role": "user", "content": "usr"}]
    assert sent["reasoning"] == {"effort": "low"} and sent["store"] is False
    assert {t["name"] for t in sent["tools"]} == {"text_metrics", "emit_fields"}
    assert all(t["type"] == "function" for t in sent["tools"])
    history = second["json"]["input"]
    assert history[-2]["type"] == "function_call"
    assert history[-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": history[-1]["output"],
    }
    assert json.loads(history[-1]["output"])["data"]["characters"] == 11
    assert completion.metrics[0].cost_amount == Decimal("0.007")


def test_oai_07_the_responses_dialect_names_the_forced_function_its_own_way(
    provider: _Provider,
) -> None:
    provider.queue(_resp_calls(("emit_fields", {"a": "1"})))
    _client(provider, wire=Wire.RESPONSES).complete(
        system="s", user="u", fields=("a",), toolbox=_empty()
    )
    assert provider.requests[0]["json"]["tool_choice"] == {
        "type": "function",
        "name": "emit_fields",
    }


def test_oai_07_the_responses_json_mode_reads_the_output_text(provider: _Provider) -> None:
    provider.queue(
        (
            200,
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"a": "x"}'}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    completion = _client(provider, wire=Wire.RESPONSES, structured=Structured.JSON).complete(
        system="s", user="u", fields=("a",), toolbox=_empty()
    )
    assert dict(completion.fields) == {"a": "x"}
    assert provider.requests[0]["json"]["text"] == {"format": {"type": "json_object"}}


def test_oai_08_the_tool_call_budget_bounds_the_loop(provider: _Provider) -> None:
    provider.queue(
        _chat_calls(("text_metrics", {"text": "a"}), ("text_metrics", {"text": "b"})),
    )
    with pytest.raises(LLMError, match="budget exhausted"):
        _client(provider, max_tool_calls=1).complete(
            system="s", user="u", fields=("a",), toolbox=_metrics_toolbox()
        )
    assert len(provider.requests) == 1


def test_oai_the_client_runs_a_real_executor_end_to_end(provider: _Provider) -> None:
    provider.queue(_chat_calls(("emit_fields", {"a": "hello"}), usage=_usage(40, 8)))
    executor = LLMTaskExecutor(
        client=_client(provider),
        system_prompt="You write.",
        user_template="Task: {input}",
        schema_ref="s@v1",
        output_fields=("a",),
        prompt_ref="p@v1",
    )
    result = executor.execute("say hi")
    assert result.succeeded and dict(result.payload_fields or {}) == {"a": "hello"}
    (measurement,) = result.analytics
    assert measurement.provider == "xai" and measurement.prompt_ref == "p@v1"
    assert provider.requests[0]["json"]["messages"][1]["content"] == "Task: say hi"


def test_oai_construction_refuses_nonsense() -> None:
    base: dict[str, Any] = {"model": "m", "pricing": _PRICING, "max_tokens": 10}
    with pytest.raises(ValueError, match="http"):
        OpenAICompatibleLLMClient(**base, base_url="ftp://x")
    with pytest.raises(ValueError, match="max_tokens"):
        OpenAICompatibleLLMClient(**{**base, "max_tokens": 0}, base_url="http://x")
    with pytest.raises(ValueError, match="effort"):
        OpenAICompatibleLLMClient(**base, base_url="http://x", effort="extreme")


# --- PRV --------------------------------------------------------------------------------------

_ROLE = "qa_agent@v1"
_TOKEN = "QA_AGENT_V1"


def _binding(provider: str, **extra: str) -> dict[str, str]:
    env = {
        f"OMEMO_PROVIDER__{_TOKEN}": provider,
        f"OMEMO_MODEL__{_TOKEN}": "grok-4",
        f"OMEMO_INPUT_PRICE_PER_MILLION__{_TOKEN}": "3",
        f"OMEMO_OUTPUT_PRICE_PER_MILLION__{_TOKEN}": "15",
        f"OMEMO_PRICE_CURRENCY__{_TOKEN}": "USD",
        f"OMEMO_MAX_TOKENS__{_TOKEN}": "8000",
        f"OMEMO_THINKING__{_TOKEN}": "inherit",
    }
    env.update(extra)
    return env


def test_prv_01_a_preset_resolves_to_its_base_url_and_key_variable() -> None:
    client = client_for_role(_ROLE, _binding("xai", XAI_API_KEY=_KEY))
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.base_url == PROVIDER_PRESETS["xai"].base_url == "https://api.x.ai/v1"
    assert client.api_key == _KEY and client.provider == "xai"
    assert client.model == "grok-4" and client.max_tokens == 8000
    assert _KEY not in repr(client)


def test_prv_01_every_preset_builds_and_openai_takes_the_newer_token_field() -> None:
    for name, preset in PROVIDER_PRESETS.items():
        env = _binding(name)
        if preset.key_env:
            env[preset.key_env] = "k"
        client = client_for_role(_ROLE, env)
        assert isinstance(client, OpenAICompatibleLLMClient)
        assert client.base_url == preset.base_url
    openai = client_for_role(_ROLE, _binding("openai", OPENAI_API_KEY="k"))
    assert isinstance(openai, OpenAICompatibleLLMClient)
    assert openai.token_param == "max_completion_tokens"


def test_prv_01_a_custom_endpoint_needs_its_base_url_and_names_its_key_variable() -> None:
    with pytest.raises(ProviderModelSelectionError, match="a base URL"):
        client_for_role(_ROLE, _binding("openai-compatible"))
    env = _binding(
        "openai-compatible",
        **{
            f"OMEMO_BASE_URL__{_TOKEN}": "https://llm.example.com/v1",
            f"OMEMO_API_KEY_ENV__{_TOKEN}": "MY_GATEWAY_KEY",
            "MY_GATEWAY_KEY": _KEY,
        },
    )
    client = client_for_role(_ROLE, env)
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.base_url == "https://llm.example.com/v1" and client.api_key == _KEY


def test_prv_01_a_preset_can_be_moved_and_the_wire_and_structure_chosen() -> None:
    env = _binding(
        "xai",
        XAI_API_KEY="k",
        **{
            f"OMEMO_BASE_URL__{_TOKEN}": "https://proxy.example/v1",
            f"OMEMO_WIRE__{_TOKEN}": "responses",
            f"OMEMO_STRUCTURED__{_TOKEN}": "json",
            f"OMEMO_TOKEN_PARAM__{_TOKEN}": "max_completion_tokens",
        },
    )
    client = client_for_role(_ROLE, env)
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.base_url == "https://proxy.example/v1"
    assert client.wire is Wire.RESPONSES and client.structured is Structured.JSON
    assert client.token_param == "max_completion_tokens"
    bad = _binding("xai", XAI_API_KEY="k", **{f"OMEMO_WIRE__{_TOKEN}": "soap"})
    with pytest.raises(ProviderModelSelectionError, match="unknown wire 'soap'"):
        client_for_role(_ROLE, bad)


def test_prv_02_the_default_fills_missing_values_and_a_role_value_wins() -> None:
    env = {
        "OMEMO_PROVIDER__DEFAULT": "xai",
        "OMEMO_MODEL__DEFAULT": "grok-default",
        "OMEMO_INPUT_PRICE_PER_MILLION__DEFAULT": "3",
        "OMEMO_OUTPUT_PRICE_PER_MILLION__DEFAULT": "15",
        "OMEMO_PRICE_CURRENCY__DEFAULT": "USD",
        "OMEMO_MAX_TOKENS__DEFAULT": "8000",
        "OMEMO_THINKING__DEFAULT": "inherit",
        "XAI_API_KEY": "k",
        f"OMEMO_MODEL__{_TOKEN}": "grok-special",
    }
    special = client_for_role(_ROLE, env)
    plain = client_for_role("script_writer@v1", env)
    assert isinstance(special, OpenAICompatibleLLMClient)
    assert isinstance(plain, OpenAICompatibleLLMClient)
    assert special.model == "grok-special" and plain.model == "grok-default"


def test_prv_02_a_role_with_neither_a_binding_nor_a_default_fails_closed() -> None:
    with pytest.raises(ProviderModelSelectionError, match="no provider/model binding"):
        client_for_role(_ROLE, {})


def test_prv_02_a_blank_role_value_falls_back_to_the_default() -> None:
    env = {**_binding("xai", XAI_API_KEY="k"), f"OMEMO_MODEL__{_TOKEN}": "  "}
    env["OMEMO_MODEL__DEFAULT"] = "from-default"
    client = client_for_role(_ROLE, env)
    assert isinstance(client, OpenAICompatibleLLMClient) and client.model == "from-default"


def test_prv_03_pricing_budget_and_thinking_stay_mandatory_for_the_family() -> None:
    for name in (
        "OMEMO_MODEL",
        "OMEMO_INPUT_PRICE_PER_MILLION",
        "OMEMO_OUTPUT_PRICE_PER_MILLION",
        "OMEMO_PRICE_CURRENCY",
        "OMEMO_MAX_TOKENS",
        "OMEMO_THINKING",
    ):
        env = _binding("xai", XAI_API_KEY="k")
        del env[f"{name}__{_TOKEN}"]
        with pytest.raises(ProviderModelSelectionError, match="selects provider 'xai' without"):
            client_for_role(_ROLE, env)


def test_prv_03_an_anthropic_only_thinking_word_is_refused_by_name() -> None:
    for word in ("adaptive", "budget:2048"):
        env = _binding("xai", XAI_API_KEY="k", **{f"OMEMO_THINKING__{_TOKEN}": word})
        with pytest.raises(ProviderModelSelectionError, match="Anthropic"):
            client_for_role(_ROLE, env)
    ok = _binding("xai", XAI_API_KEY="k", **{f"OMEMO_THINKING__{_TOKEN}": "effort:high"})
    client = client_for_role(_ROLE, ok)
    assert isinstance(client, OpenAICompatibleLLMClient) and client.effort == "high"


def test_prv_03_an_unknown_provider_lists_the_known_ones() -> None:
    with pytest.raises(ProviderModelSelectionError, match=r"known: .*xai"):
        client_for_role(_ROLE, _binding("skynet"))


def test_prv_04_a_missing_key_variable_is_named_and_its_value_never_shown() -> None:
    with pytest.raises(ProviderModelSelectionError, match="XAI_API_KEY") as raised:
        client_for_role(_ROLE, _binding("xai"))
    assert "not set" in str(raised.value)
    blank = _binding("xai", XAI_API_KEY="   ")
    with pytest.raises(ProviderModelSelectionError, match="XAI_API_KEY"):
        client_for_role(_ROLE, blank)


def test_prv_05_a_keyless_local_preset_builds_without_any_key() -> None:
    client = client_for_role(_ROLE, _binding("ollama", **{f"OMEMO_MODEL__{_TOKEN}": "llama3.1"}))
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.api_key is None and client.base_url == "http://localhost:11434/v1"


def test_prv_fake_and_anthropic_selection_are_unchanged() -> None:
    from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient

    assert isinstance(client_for_role(_ROLE, {f"OMEMO_PROVIDER__{_TOKEN}": "fake"}), FakeLLMClient)
