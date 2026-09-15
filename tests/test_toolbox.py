"""Tests for the ``Toolbox`` — scoping an agent to its granted Tools (ADR-0022 §4).

Maps TOOL_ACCEPTANCE.md §3 (TBX). A recording spy Tool proves the fail-closed half of every
refusal: the Tool was never run. The "model" is a scripted sequence of ``ToolCall`` — no LLM.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.tool import (
    ToolDescriptor,
    ToolGrantError,
    ToolParameter,
    ToolParameterType,
    ToolVersion,
)
from omemo_content_factory.tools.contract import (
    Tool,
    ToolArguments,
    ToolCall,
    ToolExecutionError,
    ToolResult,
    ToolResultStatus,
    ToolValue,
)
from omemo_content_factory.tools.current_date import CurrentDate
from omemo_content_factory.tools.text_metrics import TextMetrics
from omemo_content_factory.tools.toolbox import Toolbox


class _SpyTool:
    """A Tool that records every call it receives — proves when a Tool did *not* run."""

    def __init__(
        self, tool_id: str, *, version: int = 1, fail_with: Exception | None = None
    ) -> None:
        self.descriptor = ToolDescriptor(
            tool_id=tool_id,
            version=ToolVersion(version),
            description=f"Spy tool {tool_id}.",
            parameters=(
                ToolParameter(name="text", kind=ToolParameterType.STRING, description="Text."),
                ToolParameter(
                    name="limit",
                    kind=ToolParameterType.INTEGER,
                    description="Limit.",
                    required=False,
                ),
                ToolParameter(
                    name="strict",
                    kind=ToolParameterType.BOOLEAN,
                    description="Strict.",
                    required=False,
                ),
            ),
        )
        self.calls: list[ToolArguments] = []
        self._fail_with = fail_with

    def invoke(self, arguments: ToolArguments, /) -> Mapping[str, ToolValue]:
        self.calls.append(arguments)
        if self._fail_with is not None:
            raise self._fail_with
        return {"tool": self.descriptor.tool_id, "received": len(arguments)}


def _toolbox(*grants: str, available: list[Tool]) -> Toolbox:
    return Toolbox(grants=grants, available=available)


def _fixed_clock() -> datetime:
    return datetime(2026, 9, 15, 9, 30, tzinfo=UTC)


# --- Scoping -----------------------------------------------------------------------------


def test_tbx_01_only_granted_tools_are_exposed_in_grant_order() -> None:
    alpha, beta, gamma = _SpyTool("alpha"), _SpyTool("beta"), _SpyTool("gamma")
    toolbox = _toolbox("gamma@v1", "alpha@v1", available=[alpha, beta, gamma])
    assert toolbox.descriptors == (gamma.descriptor, alpha.descriptor)


@pytest.mark.parametrize("name", ["beta", "unknown_tool", 42, None])
def test_tbx_02_call_outside_the_grant_is_refused_and_never_runs(name: Any) -> None:
    alpha, beta = _SpyTool("alpha"), _SpyTool("beta")
    toolbox = _toolbox("alpha@v1", available=[alpha, beta])

    result = toolbox.invoke(ToolCall(name=cast(str, name), arguments={"text": "x"}))

    assert result.status is ToolResultStatus.REFUSED
    assert "not granted" in result.error
    assert "granted: alpha" in result.error
    assert result.data == {}
    assert alpha.calls == [] and beta.calls == []


@pytest.mark.parametrize(
    ("grants", "available"),
    [
        (("missing@v1",), [_SpyTool("alpha")]),
        (("alpha@v1", "alpha@v1"), [_SpyTool("alpha")]),
        (("alpha@v1", "alpha@v2"), [_SpyTool("alpha"), _SpyTool("alpha", version=2)]),
        (("alpha@v1",), [_SpyTool("alpha"), _SpyTool("alpha")]),
        ("alpha@v1", [_SpyTool("alpha")]),
    ],
    ids=["not-available", "repeated-ref", "two-versions", "duplicate-available", "bare-str"],
)
def test_tbx_03_a_grant_that_cannot_be_honoured_fails_at_construction(
    grants: Any, available: list[Tool]
) -> None:
    with pytest.raises(ToolGrantError):
        Toolbox(grants=grants, available=available)


def test_tbx_04_an_empty_grant_exposes_nothing_and_refuses_everything() -> None:
    alpha = _SpyTool("alpha")
    toolbox = _toolbox(available=[alpha])
    assert toolbox.descriptors == ()
    result = toolbox.invoke(ToolCall(name="alpha", arguments={"text": "x"}))
    assert result.status is ToolResultStatus.REFUSED
    assert "granted: none" in result.error
    assert alpha.calls == []


# --- Arguments ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ("text", "object of named values"),
        (["text"], "object of named values"),
        ({"text": "x", "extra": 1}, "unknown argument(s): extra"),
        ({}, "missing required argument 'text'"),
        ({"limit": 3}, "missing required argument 'text'"),
        ({"text": 5}, "'text' must be a string"),
        ({"text": "x", "limit": True}, "'limit' must be a integer"),
        ({"text": "x", "limit": "3"}, "'limit' must be a integer"),
        ({"text": "x", "limit": 2.5}, "'limit' must be a integer"),
        ({"text": "x", "strict": 1}, "'strict' must be a boolean"),
    ],
)
def test_tbx_05_ill_formed_arguments_are_refused_and_never_run(arguments: Any, reason: str) -> None:
    spy = _SpyTool("alpha")
    result = _toolbox("alpha@v1", available=[spy]).invoke(
        ToolCall(name="alpha", arguments=arguments)
    )
    assert result.status is ToolResultStatus.REFUSED
    assert reason in result.error
    assert spy.calls == []


def test_tbx_06_the_tool_receives_only_declared_arguments_read_only() -> None:
    spy = _SpyTool("alpha")
    toolbox = _toolbox("alpha@v1", available=[spy])
    raw: dict[str, object] = {"text": "draft", "limit": 3}

    toolbox.invoke(ToolCall(name="alpha", arguments=raw))
    raw["text"] = "changed after the call"

    (received,) = spy.calls
    assert dict(received) == {"text": "draft", "limit": 3}  # optional 'strict' may be omitted
    with pytest.raises(TypeError):
        cast(Any, received)["text"] = "tampered"


# --- Outcomes ----------------------------------------------------------------------------


def test_tbx_07_a_tool_execution_error_becomes_a_failed_result() -> None:
    spy = _SpyTool("alpha", fail_with=ToolExecutionError("upstream unavailable"))
    result = _toolbox("alpha@v1", available=[spy]).invoke(
        ToolCall(name="alpha", arguments={"text": "x"})
    )
    assert result.status is ToolResultStatus.FAILED
    assert result.error == "Tool 'alpha' failed: upstream unavailable"
    assert result.data == {}
    assert len(spy.calls) == 1  # it did run


def test_tbx_07_any_other_exception_is_a_defect_and_propagates() -> None:
    spy = _SpyTool("alpha", fail_with=RuntimeError("bug"))
    toolbox = _toolbox("alpha@v1", available=[spy])
    with pytest.raises(RuntimeError, match="bug"):
        toolbox.invoke(ToolCall(name="alpha", arguments={"text": "x"}))


def test_tbx_08_a_granted_well_formed_call_returns_the_tool_data() -> None:
    spy = _SpyTool("alpha")
    result = _toolbox("alpha@v1", available=[spy]).invoke(
        ToolCall(name="alpha", arguments={"text": "x", "strict": True})
    )
    assert result == ToolResult.ok({"tool": "alpha", "received": 2})
    with pytest.raises(TypeError):
        cast(Any, result.data)["tool"] = "tampered"


# --- Agent configuration + one reasoning step --------------------------------------------


def test_tbx_09_the_grant_comes_from_the_agent_configuration() -> None:
    library: list[Tool] = [CurrentDate(clock=_fixed_clock), TextMetrics()]

    plain = Agent(agent_id="researcher@v1", name="Researcher", prompt_ref="research")
    assert plain.tool_refs == ()
    assert Toolbox(grants=plain.tool_refs, available=library).descriptors == ()

    writer = Agent(
        agent_id="writer@v1", name="Writer", prompt_ref="write", tool_refs=("text_metrics@v1",)
    )
    toolbox = Toolbox(grants=writer.tool_refs, available=library)
    assert [d.ref for d in toolbox.descriptors] == ["text_metrics@v1"]


def test_tbx_10_a_reasoning_step_gets_results_back_and_stays_inside_its_grant() -> None:
    writer = Agent(
        agent_id="writer@v1", name="Writer", prompt_ref="write", tool_refs=("text_metrics@v1",)
    )
    toolbox = Toolbox(
        grants=writer.tool_refs, available=[CurrentDate(clock=_fixed_clock), TextMetrics()]
    )
    # A scripted model: it tries a Tool it was not granted, then measures its draft.
    scripted_calls = [
        ToolCall(name="current_date", arguments={}),
        ToolCall(name="text_metrics", arguments={"text": "Пейте воду. Спите.", "max_chars": 20}),
    ]

    transcript = [(call.name, toolbox.invoke(call)) for call in scripted_calls]

    (date_name, date_result), (metrics_name, metrics_result) = transcript
    assert (date_name, date_result.status) == ("current_date", ToolResultStatus.REFUSED)
    assert (metrics_name, metrics_result.status) == ("text_metrics", ToolResultStatus.OK)
    assert metrics_result.data["sentences"] == 2
    assert metrics_result.data["within_limit"] is True
