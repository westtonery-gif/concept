"""Behavioural tests for the Analytics Record entity (ADR-0020).

Everything is driven **only through the Run aggregate root** (``record_analytics`` and the read-only
``analytics_records`` / ``analytics_record``). The central rules: a record is immutable and
append-only, and its attribution — Run, Task, role and retries — is derived from the owned Task,
never asserted by the caller (DOMAIN_MODEL.md §2.15, §6). Scenario ids refer to
``ANALYTICS_RECORD_ACCEPTANCE.md``. No mocks, system clock, sleep or randomness.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from omemo_content_factory.domain.analytics import (
    AnalyticsRecordCaptured,
    Cost,
    InvalidAnalyticsRecordError,
    TimeRange,
    TokenUsage,
)
from omemo_content_factory.domain.run import Actor, Run, RunStatus, UnauthorizedActorError
from omemo_content_factory.domain.task import TaskStatus

CD = Actor.CONTENT_DIRECTOR
AGENT_REF = "script_writer@v1"
T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
USAGE = TokenUsage(input_tokens=1200, output_tokens=350)
COST = Cost(amount=Decimal("0.0123"), currency="USD")
SPAN = TimeRange(started_at=T0, finished_at=T0 + timedelta(milliseconds=1500))


def make_run(run_id: str = "run-an-0001") -> Run:
    """A fresh Run in CREATED."""
    return Run.create(
        run_id=run_id, content_brief_ref="brief-0001", workflow_version_ref="workflow@v1"
    )


def open_task(run: Run) -> str:
    """Open a PENDING Task on ``run``; return its id."""
    return run.open_task(workflow_step_ref="step-1", agent_ref=AGENT_REF, task_input="brief", by=CD)


def started_task(run: Run) -> str:
    """Open a Task on ``run`` and start it (attempt 1); return its id."""
    task_id = open_task(run)
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    return task_id


def capture(
    run: Run,
    task_id: str,
    *,
    by: Actor = CD,
    provider: str = "anthropic",
    model: str = "claude-sonnet-5",
    prompt_ref: str | None = None,
) -> str:
    """Record one call's metrics for ``task_id``; return the record id."""
    return run.record_analytics(
        task_id,
        provider=provider,
        model=model,
        token_usage=USAGE,
        cost=COST,
        time_range=SPAN,
        by=by,
        prompt_ref=prompt_ref,
    )


# --- Happy path (AHP) --------------------------------------------------------------------


def test_ahp_01_a_call_is_recorded_with_attribution_derived_from_the_task() -> None:
    run = make_run()
    task_id = started_task(run)
    record_id = capture(run, task_id)
    record = run.analytics_record(record_id)
    assert record_id == f"{run.run_id}-analytics-1"
    assert record.record_id == record_id
    assert (record.run_id, record.task_id, record.agent_ref) == (run.run_id, task_id, AGENT_REF)
    assert (record.provider, record.model) == ("anthropic", "claude-sonnet-5")
    assert (record.token_usage, record.cost, record.time_range) == (USAGE, COST, SPAN)
    assert record.retries == 0
    assert record.prompt_ref is None
    assert run.analytics_records == (record,)
    assert run.events[-1] == AnalyticsRecordCaptured(
        run_id=run.run_id, record_id=record_id, task_id=task_id, agent_ref=AGENT_REF
    )


def test_ahp_02_retries_are_the_tasks_earlier_attempts_at_capture() -> None:
    run = make_run()
    task_id = started_task(run)
    first = capture(run, task_id)
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)  # retry -> attempt 2
    second = capture(run, task_id)
    assert run.analytics_record(first).retries == 0
    assert run.analytics_record(second).retries == 1


def test_ahp_03_several_calls_of_one_task_give_several_records_in_order() -> None:
    run = make_run()
    task_id = started_task(run)
    ids = [capture(run, task_id) for _ in range(3)]
    assert len(set(ids)) == 3
    assert [record.record_id for record in run.analytics_records] == ids
    assert {record.task_id for record in run.analytics_records} == {task_id}


def test_ahp_04_a_failed_call_is_recorded_even_after_the_run_has_ended() -> None:
    run = make_run()
    task_id = started_task(run)
    run.transition_task(task_id, TaskStatus.FAILED, by=CD, reason="provider timeout")
    run.transition(RunStatus.FAILED, by=CD, reason="step failed")
    record_id = capture(run, task_id)
    assert run.analytics_record(record_id).task_id == task_id


def test_ahp_05_the_prompt_version_is_kept_when_given() -> None:
    run = make_run()
    record_id = capture(run, started_task(run), prompt_ref="script_writer.prompt@v1")
    assert run.analytics_record(record_id).prompt_ref == "script_writer.prompt@v1"


def test_ahp_06_derived_values() -> None:
    assert USAGE.total_tokens == 1550
    assert SPAN.duration == timedelta(milliseconds=1500)
    assert TimeRange(started_at=T0, finished_at=T0).duration == timedelta(0)


# --- Violations (AFL) --------------------------------------------------------------------


@pytest.mark.parametrize("actor", [Actor.AGENT, Actor.HUMAN_REVIEWER])
def test_afl_01_only_content_director_records_metrics(actor: Actor) -> None:
    run = make_run()
    task_id = started_task(run)
    events_before = len(run.events)
    with pytest.raises(UnauthorizedActorError):
        capture(run, task_id, by=actor)
    assert run.analytics_records == ()
    assert len(run.events) == events_before


def test_afl_02_the_task_must_be_owned_by_the_run() -> None:
    run = make_run()
    other = make_run("run-an-0002")
    foreign_task = started_task(other)
    with pytest.raises(KeyError):
        capture(run, f"{run.run_id}-task-99")
    with pytest.raises(KeyError):
        capture(run, foreign_task)
    assert run.analytics_records == ()


@pytest.mark.parametrize("skip", [False, True], ids=["pending", "skipped"])
def test_afl_03_a_task_that_never_started_made_no_call(skip: bool) -> None:
    run = make_run()
    task_id = open_task(run)
    if skip:
        run.transition_task(task_id, TaskStatus.SKIPPED, by=CD)
    with pytest.raises(InvalidAnalyticsRecordError):
        capture(run, task_id)
    assert run.analytics_records == ()


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: TokenUsage(input_tokens=-1, output_tokens=0), id="negative-input"),
        pytest.param(lambda: TokenUsage(input_tokens=0, output_tokens=-1), id="negative-output"),
        pytest.param(lambda: TokenUsage(input_tokens=True, output_tokens=0), id="bool-count"),
        pytest.param(lambda: Cost(amount=Decimal("-0.01"), currency="USD"), id="negative-cost"),
        pytest.param(
            lambda: Cost(amount=0.01, currency="USD"),  # type: ignore[arg-type]
            id="float-cost",
        ),
        pytest.param(lambda: Cost(amount=Decimal("NaN"), currency="USD"), id="nan-cost"),
        pytest.param(lambda: Cost(amount=Decimal("Infinity"), currency="USD"), id="infinite-cost"),
        pytest.param(lambda: Cost(amount=Decimal("1"), currency=" "), id="blank-currency"),
        pytest.param(
            lambda: TimeRange(started_at=datetime(2026, 9, 15, 12), finished_at=T0),
            id="naive-start",
        ),
        pytest.param(
            lambda: TimeRange(started_at=T0, finished_at=datetime(2026, 9, 15, 13)),
            id="naive-finish",
        ),
        pytest.param(
            lambda: TimeRange(started_at=T0, finished_at=T0 - timedelta(seconds=1)),
            id="finished-before-started",
        ),
    ],
)
def test_afl_04_implausible_metric_values_are_refused(build: Callable[[], object]) -> None:
    with pytest.raises(InvalidAnalyticsRecordError):
        build()


@pytest.mark.parametrize(
    ("provider", "model", "prompt_ref"),
    [
        (" ", "claude-sonnet-5", None),
        ("anthropic", "", None),
        ("anthropic", "claude-sonnet-5", " "),
    ],
    ids=["blank-provider", "blank-model", "blank-prompt-ref"],
)
def test_afl_05_blank_references_are_refused_without_consuming_an_id(
    provider: str, model: str, prompt_ref: str | None
) -> None:
    run = make_run()
    task_id = started_task(run)
    events_before = len(run.events)
    with pytest.raises(InvalidAnalyticsRecordError):
        capture(run, task_id, provider=provider, model=model, prompt_ref=prompt_ref)
    assert run.analytics_records == ()
    assert len(run.events) == events_before
    assert capture(run, task_id) == f"{run.run_id}-analytics-1"


def test_afl_06_records_and_their_values_are_immutable() -> None:
    run = make_run()
    record = run.analytics_record(capture(run, started_task(run)))
    targets: tuple[tuple[object, str], ...] = (
        (record, "retries"),
        (record, "cost"),
        (USAGE, "input_tokens"),
        (COST, "amount"),
        (SPAN, "finished_at"),
    )
    for target, name in targets:
        with pytest.raises(FrozenInstanceError):
            setattr(target, name, None)


# --- Aggregate boundary (AAG) ------------------------------------------------------------


def test_aag_01_a_record_belongs_to_exactly_one_run() -> None:
    first, second = make_run("run-an-0001"), make_run("run-an-0002")
    record_id = capture(first, started_task(first))
    started_task(second)
    assert first.analytics_record(record_id).run_id == first.run_id
    assert record_id.startswith(f"{first.run_id}-")
    assert second.analytics_records == ()
    with pytest.raises(KeyError):
        second.analytics_record(record_id)
