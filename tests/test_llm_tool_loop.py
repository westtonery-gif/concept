"""Acceptance tests for the bounded LLM Tool loop (`ADR-0028`, TOOL_ACCEPTANCE LTL)."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import anthropic
import pytest
from anthropic.types import ContentBlock, Message, ToolParam, ToolUseBlock, Usage

from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.skill_execution import SkillPreprocessingTaskExecutor
from omemo_content_factory.composition import build_available_tools, build_executor_map
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.tool import ToolGrantError
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.llm import (
    AnthropicLLMClient,
    LLMError,
    LLMTaskExecutor,
    TokenPricing,
)
from omemo_content_factory.tools.contract import Tool
from omemo_content_factory.tools.current_date import CurrentDate
from omemo_content_factory.tools.text_metrics import TextMetrics
from omemo_content_factory.tools.toolbox import Toolbox

_PRICING = TokenPricing(Decimal("3"), Decimal("15"), "USD")


class _ScriptedMessages:
    """Provider endpoint with scripted Messages and immutable snapshots of every request."""

    def __init__(self, responses: list[Message]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> Message:
        self.calls.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("unexpected provider turn")
        return self.responses.pop(0)


class _ScriptedAnthropic:
    def __init__(self, responses: list[Message]) -> None:
        self.messages = _ScriptedMessages(responses)


def _message(*blocks: ContentBlock) -> Message:
    return Message(
        id="msg_test",
        content=list(blocks),
        model="test-model",
        role="assistant",
        stop_reason="tool_use",
        stop_sequence=None,
        type="message",
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _call(call_id: str, name: str, arguments: dict[str, object] | None = None) -> ToolUseBlock:
    return ToolUseBlock(
        id=call_id,
        input={} if arguments is None else arguments,
        name=name,
        type="tool_use",
    )


def _client(
    responses: list[Message], *, max_tool_calls: int = 8
) -> tuple[AnthropicLLMClient, _ScriptedMessages]:
    scripted = _ScriptedAnthropic(responses)
    client = AnthropicLLMClient(
        model="configured-model",
        pricing=_PRICING,
        max_tool_calls=max_tool_calls,
        client=cast(anthropic.Anthropic, scripted),
    )
    return client, scripted.messages


def _current_date_toolbox(clock: Callable[[], datetime]) -> Toolbox:
    tool: Tool = CurrentDate(clock)
    return Toolbox(grants=(tool.descriptor.ref,), available=(tool,))


def _tool_result_payload(
    endpoint: _ScriptedMessages, turn: int = 1
) -> tuple[dict[str, object], bool]:
    messages = cast(list[dict[str, object]], endpoint.calls[turn]["messages"])
    content = cast(list[dict[str, object]], messages[-1]["content"])
    return json.loads(cast(str, content[0]["content"])), cast(bool, content[0]["is_error"])


def test_ltl_01_current_date_runs_mid_reasoning_and_result_reaches_next_turn() -> None:
    clock_calls: list[None] = []

    def clock() -> datetime:
        clock_calls.append(None)
        return datetime(2026, 9, 15, 18, 30, tzinfo=UTC)

    client, endpoint = _client(
        [
            _message(_call("date-1", "current_date")),
            _message(_call("final-1", "emit_fields", {"audience": "A", "angle": "B"})),
        ]
    )

    result = client.complete(
        system="research",
        user="What should we publish this week?",
        fields=("audience", "angle"),
        toolbox=_current_date_toolbox(clock),
    )

    assert result.fields == {"audience": "A", "angle": "B"}
    assert clock_calls == [None]
    assert len(endpoint.calls) == 2
    payload, is_error = _tool_result_payload(endpoint)
    assert payload == {
        "status": "ok",
        "data": {
            "date": "2026-09-15",
            "weekday": "Tuesday",
            "datetime": "2026-09-15T18:30:00+00:00",
        },
        "error": "",
    }
    assert is_error is False


def test_ltl_02_only_granted_descriptors_are_translated_with_parameter_contract() -> None:
    tool: Tool = TextMetrics()
    toolbox = Toolbox(grants=(tool.descriptor.ref,), available=build_available_tools())
    client, endpoint = _client([_message(_call("final", "emit_fields", {"text": "done"}))])

    client.complete(system="s", user="u", fields=("text",), toolbox=toolbox)

    tools = cast(list[ToolParam], endpoint.calls[0]["tools"])
    assert [item["name"] for item in tools] == ["text_metrics", "emit_fields"]
    schema = cast(dict[str, object], tools[0]["input_schema"])
    assert schema["required"] == ["text"]
    properties = cast(dict[str, dict[str, object]], schema["properties"])
    assert properties["text"]["type"] == "string"
    assert properties["max_chars"]["type"] == "integer"
    assert schema["additionalProperties"] is False


def test_ltl_03_unknown_call_is_refused_and_returned_to_model() -> None:
    client, endpoint = _client(
        [
            _message(_call("bad-1", "not_granted")),
            _message(_call("final", "emit_fields", {"text": "recovered"})),
        ]
    )
    result = client.complete(
        system="s",
        user="u",
        fields=("text",),
        toolbox=_current_date_toolbox(lambda: datetime(2026, 9, 15, tzinfo=UTC)),
    )

    payload, is_error = _tool_result_payload(endpoint)
    assert result.fields == {"text": "recovered"}
    assert payload["status"] == "refused"
    assert "not granted" in cast(str, payload["error"])
    assert is_error is True


def test_ltl_04_tool_failure_is_returned_and_model_can_finish() -> None:
    client, endpoint = _client(
        [
            _message(_call("date-1", "current_date")),
            _message(_call("final", "emit_fields", {"text": "fallback"})),
        ]
    )
    result = client.complete(
        system="s",
        user="u",
        fields=("text",),
        toolbox=_current_date_toolbox(lambda: datetime(2026, 9, 15)),
    )

    payload, is_error = _tool_result_payload(endpoint)
    assert result.fields == {"text": "fallback"}
    assert payload["status"] == "failed"
    assert "naive datetime" in cast(str, payload["error"])
    assert is_error is True


def test_ltl_05_over_budget_batch_runs_no_tool_and_executor_fails_managed() -> None:
    clock_calls: list[None] = []

    def clock() -> datetime:
        clock_calls.append(None)
        return datetime(2026, 9, 15, tzinfo=UTC)

    client, _ = _client(
        [_message(_call("date-1", "current_date"), _call("date-2", "current_date"))],
        max_tool_calls=1,
    )
    executor = LLMTaskExecutor(
        client=client,
        system_prompt="s",
        user_template="{input}",
        schema_ref="x@1",
        output_fields=("text",),
        prompt_ref="test@v1",
        toolbox=_current_date_toolbox(clock),
    )

    result = executor.execute("brief")

    assert result.succeeded is False
    assert result.failure_reason is not None and "budget exhausted" in result.failure_reason
    assert clock_calls == []


def test_ltl_06_mixed_final_and_operational_calls_fail_before_tool_runs() -> None:
    clock_calls: list[None] = []

    def clock() -> datetime:
        clock_calls.append(None)
        return datetime(2026, 9, 15, tzinfo=UTC)

    client, _ = _client(
        [_message(_call("date", "current_date"), _call("final", "emit_fields", {"x": "y"}))]
    )
    with pytest.raises(LLMError, match="mixed"):
        client.complete(system="s", user="u", fields=("x",), toolbox=_current_date_toolbox(clock))
    assert clock_calls == []


def test_ltl_07_empty_toolbox_keeps_single_forced_structured_call() -> None:
    client, endpoint = _client([_message(_call("final", "emit_fields", {"x": "y"}))])

    result = client.complete(
        system="s", user="u", fields=("x",), toolbox=Toolbox(grants=(), available=())
    )

    assert result.fields == {"x": "y"}
    assert len(endpoint.calls) == 1
    assert endpoint.calls[0]["tool_choice"] == {"type": "tool", "name": "emit_fields"}


def test_ltl_08_composition_builds_each_agent_scoped_toolbox_and_rejects_bad_grant() -> None:
    tools = build_available_tools(clock=lambda: datetime(2026, 9, 15, tzinfo=UTC))
    rin_executor = build_executor_map(
        rin.AGENTS,
        rin.PROMPTS,
        FakeLLMClient(),
        rin.SCHEMAS,
        skill_invocations=rin.SKILL_INVOCATIONS,
        available_tools=tools,
    )[rin.AGENT_REF]
    assert isinstance(rin_executor, SkillPreprocessingTaskExecutor)
    assert isinstance(rin_executor.delegate, LLMTaskExecutor)
    assert [d.ref for d in rin_executor.delegate.toolbox.descriptors] == ["current_date@v1"]

    leo_executor = build_executor_map(
        leo.AGENTS, leo.PROMPTS, FakeLLMClient(), leo.SCHEMAS, available_tools=tools
    )[leo.AGENT_REF]
    assert isinstance(leo_executor, LLMTaskExecutor)
    assert leo_executor.toolbox.descriptors == ()

    bad_agent = Agent(
        agent_id="bad@v1", name="bad", prompt_ref=leo.PROMPT_REF, tool_refs=("missing@v1",)
    )
    with pytest.raises(ToolGrantError, match="not among"):
        build_executor_map(
            [bad_agent], leo.PROMPTS, FakeLLMClient(), leo.SCHEMAS, available_tools=tools
        )


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
def test_ltl_call_budget_must_be_a_positive_integer(value: object) -> None:
    with pytest.raises(ValueError, match="max_tool_calls"):
        AnthropicLLMClient(model="configured", pricing=_PRICING, max_tool_calls=cast(int, value))
