"""Acceptance tests for LLM metrics capture (`ADR-0029`, ANALYTICS acceptance MTC)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic.types import ContentBlock, Message, ToolUseBlock, Usage

from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.application.content_director import ContentDirector, TaskRequest
from omemo_content_factory.composition import build_content_director
from omemo_content_factory.domain.analytics import AnalyticsRecordCaptured
from omemo_content_factory.domain.run import Actor, Run, RunStatus
from omemo_content_factory.domain.task import TaskCompleted, TaskStatus
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.llm import (
    AnthropicLLMClient,
    LLMError,
    LLMTaskExecutor,
    TokenPricing,
)
from omemo_content_factory.tools.contract import Tool
from omemo_content_factory.tools.current_date import CurrentDate
from omemo_content_factory.tools.toolbox import Toolbox

_T0 = datetime(2026, 9, 15, 12, tzinfo=UTC)
_PRICING = TokenPricing(Decimal("3"), Decimal("15"), "USD")


class _ScriptedMessages:
    """Minimal Anthropic messages endpoint returning a fixed response sequence."""

    def __init__(self, responses: list[Message]) -> None:
        self.responses = list(responses)

    def create(self, **kwargs: object) -> Message:
        if not self.responses:
            raise AssertionError("unexpected provider turn")
        return self.responses.pop(0)


class _ScriptedAnthropic:
    def __init__(self, responses: list[Message]) -> None:
        self.messages = _ScriptedMessages(responses)


class _FailingMessages:
    def create(self, **kwargs: object) -> Message:
        request = httpx2.Request("POST", "https://api.anthropic.test/v1/messages")
        raise anthropic.APIConnectionError(request=request)


class _FailingAnthropic:
    def __init__(self) -> None:
        self.messages = _FailingMessages()


def _call(call_id: str, name: str, arguments: dict[str, object]) -> ToolUseBlock:
    return ToolUseBlock(id=call_id, input=arguments, name=name, type="tool_use")


def _message(
    *blocks: ContentBlock, model: str = "actual-model", input_tokens: int, output_tokens: int
) -> Message:
    return Message(
        id="msg_metrics",
        content=list(blocks),
        model=model,
        role="assistant",
        stop_reason="tool_use",
        stop_sequence=None,
        type="message",
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _clock(*moments: datetime) -> Callable[[], datetime]:
    values = iter(moments)
    return lambda: next(values)


def _client(
    responses: list[Message], *, clock: Callable[[], datetime], max_tool_calls: int = 8
) -> AnthropicLLMClient:
    scripted = _ScriptedAnthropic(responses)
    return AnthropicLLMClient(
        model="configured-alias",
        pricing=_PRICING,
        max_tool_calls=max_tool_calls,
        client=cast(anthropic.Anthropic, scripted),
        clock=clock,
    )


def _date_toolbox() -> Toolbox:
    tool: Tool = CurrentDate(lambda: _T0)
    return Toolbox(grants=(tool.descriptor.ref,), available=(tool,))


def test_mtc_01_one_response_reports_actual_usage_cost_and_latency() -> None:
    client = _client(
        [
            _message(
                _call("final", "emit_fields", {"text": "done"}),
                input_tokens=1000,
                output_tokens=200,
            )
        ],
        clock=_clock(_T0, _T0 + timedelta(milliseconds=250)),
    )

    completion = client.complete(
        system="s",
        user="u",
        fields=("text",),
        toolbox=Toolbox(grants=(), available=()),
    )

    assert completion.fields == {"text": "done"}
    assert len(completion.metrics) == 1
    metric = completion.metrics[0]
    assert (metric.provider, metric.model) == ("anthropic", "actual-model")
    assert (metric.input_tokens, metric.output_tokens) == (1000, 200)
    assert (metric.cost_amount, metric.cost_currency) == (Decimal("0.006"), "USD")
    assert metric.started_at == _T0
    assert metric.finished_at == _T0 + timedelta(milliseconds=250)


def test_mtc_02_tool_loop_keeps_one_measurement_per_provider_turn() -> None:
    client = _client(
        [
            _message(_call("date", "current_date", {}), input_tokens=100, output_tokens=10),
            _message(
                _call("final", "emit_fields", {"text": "done"}),
                model="resolved-model-v2",
                input_tokens=200,
                output_tokens=20,
            ),
        ],
        clock=_clock(
            _T0,
            _T0 + timedelta(milliseconds=10),
            _T0 + timedelta(milliseconds=20),
            _T0 + timedelta(milliseconds=50),
        ),
    )

    completion = client.complete(system="s", user="u", fields=("text",), toolbox=_date_toolbox())

    assert completion.fields == {"text": "done"}
    assert [(m.input_tokens, m.output_tokens) for m in completion.metrics] == [
        (100, 10),
        (200, 20),
    ]
    assert [m.model for m in completion.metrics] == ["actual-model", "resolved-model-v2"]
    assert [m.cost_amount for m in completion.metrics] == [
        Decimal("0.00045"),
        Decimal("0.0009"),
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_per_million": -Decimal(1)},
        {"output_per_million": Decimal("NaN")},
        {"input_per_million": 1.5},
        {"currency": " "},
    ],
)
def test_mtc_03_pricing_refuses_inexact_or_implausible_values(kwargs: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "input_per_million": Decimal(1),
        "output_per_million": Decimal(1),
        "currency": "USD",
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        TokenPricing(**values)


def test_mtc_05_06_09_composition_records_fake_call_and_exact_prompt_version() -> None:
    client = FakeLLMClient(clock=_clock(_T0, _T0 + timedelta(milliseconds=1)))
    director = build_content_director(
        rin.AGENTS,
        None,
        client,
        rin.SCHEMAS,
        skill_invocations=rin.SKILL_INVOCATIONS,
    )
    run = Run.create(run_id="metrics-fake", content_brief_ref="brief", workflow_version_ref="wf@1")

    director.execute(run, [TaskRequest("research", rin.AGENT_REF, "brief")])

    assert run.status is RunStatus.COMPLETED
    assert len(run.analytics_records) == 1
    record = run.analytics_records[0]
    assert (record.provider, record.model) == ("fake", "deterministic-placeholder@v1")
    assert record.token_usage.total_tokens == 0
    assert record.cost.amount == Decimal(0)
    assert record.prompt_ref == "content-researcher@v1"
    assert record.retries == 0
    event_types = [type(event) for event in run.events]
    assert event_types.index(AnalyticsRecordCaptured) < event_types.index(TaskCompleted)


def test_mtc_07_completed_turn_is_recorded_when_tool_loop_fails_managed() -> None:
    response = _message(
        _call("date-1", "current_date", {}),
        _call("date-2", "current_date", {}),
        input_tokens=300,
        output_tokens=30,
    )
    client = _client(
        [response],
        clock=_clock(_T0, _T0 + timedelta(milliseconds=5)),
        max_tool_calls=1,
    )
    executor = LLMTaskExecutor(
        client=client,
        system_prompt="s",
        user_template="{input}",
        schema_ref="x@v1",
        output_fields=("text",),
        prompt_ref="failure-prompt@v3",
        toolbox=_date_toolbox(),
    )
    director = ContentDirector(executor)
    run = Run.create(
        run_id="metrics-failure", content_brief_ref="brief", workflow_version_ref="wf@1"
    )

    director.execute(run, [TaskRequest("step", "agent@v1", "brief")])

    assert run.status is RunStatus.FAILED
    assert run.tasks[0].status is TaskStatus.FAILED
    assert len(run.analytics_records) == 1
    record = run.analytics_records[0]
    assert record.token_usage.input_tokens == 300
    assert record.prompt_ref == "failure-prompt@v3"
    assert "budget exhausted" in (run.tasks[0].failure_reason or "")


def test_mtc_07_transport_failure_without_response_does_not_fabricate_metrics() -> None:
    client = AnthropicLLMClient(
        model="configured-alias",
        pricing=_PRICING,
        client=cast(anthropic.Anthropic, _FailingAnthropic()),
        clock=lambda: _T0,
    )
    with pytest.raises(LLMError) as raised:
        client.complete(
            system="s",
            user="u",
            fields=("text",),
            toolbox=Toolbox(grants=(), available=()),
        )
    assert raised.value.metrics == ()


def test_mtc_08_resumed_task_call_records_current_retry_count() -> None:
    executor = LLMTaskExecutor(
        client=FakeLLMClient(clock=_clock(_T0, _T0)),
        system_prompt="s",
        user_template="{input}",
        schema_ref="x@v1",
        output_fields=("text",),
        prompt_ref="retry-prompt@v1",
    )
    director = ContentDirector(executor)
    request = TaskRequest("step", "agent@v1", "brief")
    run = Run.create(run_id="metrics-retry", content_brief_ref="brief", workflow_version_ref="wf@1")
    run.transition(RunStatus.QUEUED, by=Actor.CONTENT_DIRECTOR)
    run.transition(RunStatus.RUNNING, by=Actor.CONTENT_DIRECTOR)
    task_id = run.open_task("step", "agent@v1", "brief", by=Actor.CONTENT_DIRECTOR)
    run.transition_task(task_id, TaskStatus.RUNNING, by=Actor.CONTENT_DIRECTOR)

    director.resume(run, [request])

    assert run.task(task_id).attempt_count == 2
    assert len(run.analytics_records) == 1
    assert run.analytics_records[0].retries == 1


def test_logical_llm_error_exposes_completed_turn_metrics() -> None:
    client = _client(
        [
            _message(
                _call("date", "current_date", {}),
                _call("final", "emit_fields", {"text": "racy"}),
                input_tokens=10,
                output_tokens=2,
            )
        ],
        clock=_clock(_T0, _T0),
    )
    with pytest.raises(LLMError) as raised:
        client.complete(system="s", user="u", fields=("text",), toolbox=_date_toolbox())
    assert len(raised.value.metrics) == 1
