"""Behavioural tests for the Output entity (``omemo_content_factory.domain.output``).

Output is driven **only through the Run aggregate root** (``record_output`` and the read-only
``TaskView.output``) and through the application slice ``execute_task`` — there is no aggregate
factory elsewhere. Tests reference the rules of ADR-0005 / DOMAIN_MODEL.md §2.11. No mocks,
sleep or randomness; a deterministic fake executor stands in for real work.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult, execute_task
from omemo_content_factory.domain.output import (
    DuplicateOutputError,
    InvalidOutputStateError,
    OutputStatus,
    OutputValidated,
)
from omemo_content_factory.domain.run import Actor, Run, UnauthorizedActorError
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.task import TaskStatus


def _active_schema(required: tuple[str, ...] = ("body",)) -> Schema:
    schema = Schema.create(
        schema_id="draft", version=SchemaVersion(1), description="d", required_fields=list(required)
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


RUN_ID = "run-out-0001"
BRIEF_REF = "brief-0001"
WORKFLOW_REF = "workflow@v1"
STEP_REF = "step-1"
AGENT_REF = "writer@v1"
TASK_INPUT = "approved brief body"
SCHEMA_REF = "content-output@v1"
PAYLOAD = "generated content"
CD = Actor.CONTENT_DIRECTOR


@dataclass(frozen=True, slots=True)
class StaticTaskExecutor:
    """Deterministic test double returning a preconfigured result regardless of input."""

    result: ExecutionResult

    def execute(self, task_input: str) -> ExecutionResult:
        return self.result


def make_run() -> Run:
    """A fresh Run in CREATED."""
    return Run.create(
        run_id=RUN_ID,
        content_brief_ref=BRIEF_REF,
        workflow_version_ref=WORKFLOW_REF,
    )


def open_task(run: Run) -> str:
    """Open a standard Task on ``run`` and return its id."""
    return run.open_task(
        workflow_step_ref=STEP_REF,
        agent_ref=AGENT_REF,
        task_input=TASK_INPUT,
        by=CD,
    )


def succeeded_task(run: Run) -> str:
    """Open a Task and drive it to SUCCEEDED; return its id."""
    task_id = open_task(run)
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=CD)
    return task_id


# --- Recording an Output through the root ------------------------------------------------


def test_record_output_attaches_a_valid_output_to_a_succeeded_task() -> None:
    """A recorded Output is VALID, 1:1 with its Task, and readable via the Run root."""
    run = make_run()
    task_id = succeeded_task(run)
    output_id = run.record_output(task_id, payload=PAYLOAD, schema_ref=SCHEMA_REF, by=CD)
    output = run.task(task_id).output
    assert output is not None
    assert output.output_id == output_id
    assert output.task_id == task_id
    assert output.payload == PAYLOAD
    assert output.schema_ref == SCHEMA_REF
    assert output.status is OutputStatus.VALID


def test_record_output_emits_output_validated_in_the_run_log() -> None:
    """Recording an Output emits OutputValidated in the Run's single event log."""
    run = make_run()
    task_id = succeeded_task(run)
    output_id = run.record_output(task_id, payload=PAYLOAD, schema_ref=SCHEMA_REF, by=CD)
    last = run.events[-1]
    assert isinstance(last, OutputValidated)
    assert last.run_id == run.run_id
    assert last.task_id == task_id
    assert last.output_id == output_id


# --- Pure sink: persists the given verdict (3C, Variant A) -------------------------------


def test_record_output_persists_invalid_when_marked_not_valid() -> None:
    """The sink persists the provided verdict: valid=False -> INVALID, with no OutputValidated."""
    run = make_run()
    task_id = succeeded_task(run)
    output_id = run.record_output(
        task_id, payload=PAYLOAD, schema_ref=SCHEMA_REF, by=CD, valid=False
    )
    output = run.task(task_id).output
    assert output is not None
    assert output.output_id == output_id
    assert output.status is OutputStatus.INVALID
    assert not any(isinstance(event, OutputValidated) for event in run.events)


# --- Guards ------------------------------------------------------------------------------


@pytest.mark.parametrize("drive_to", [TaskStatus.RUNNING, TaskStatus.FAILED])
def test_output_only_after_a_successful_task(drive_to: TaskStatus) -> None:
    """An Output cannot be recorded for a Task that has not SUCCEEDED (ADR-0005 §5)."""
    run = make_run()
    task_id = open_task(run)
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    if drive_to is TaskStatus.FAILED:
        run.transition_task(task_id, TaskStatus.FAILED, by=CD, reason="boom")
    with pytest.raises(InvalidOutputStateError):
        run.record_output(task_id, payload=PAYLOAD, schema_ref=SCHEMA_REF, by=CD)
    assert run.task(task_id).output is None


def test_a_task_may_have_only_one_output() -> None:
    """Recording a second Output for the same Task is rejected (1:1, DOMAIN_MODEL.md §5)."""
    run = make_run()
    task_id = succeeded_task(run)
    run.record_output(task_id, payload=PAYLOAD, schema_ref=SCHEMA_REF, by=CD)
    with pytest.raises(DuplicateOutputError):
        run.record_output(task_id, payload="other", schema_ref=SCHEMA_REF, by=CD)


def test_only_content_director_can_record_an_output() -> None:
    """A non-Content-Director actor cannot record an Output (ADR-0005 §5)."""
    run = make_run()
    task_id = succeeded_task(run)
    with pytest.raises(UnauthorizedActorError):
        run.record_output(task_id, payload=PAYLOAD, schema_ref=SCHEMA_REF, by=Actor.AGENT)
    assert run.task(task_id).output is None


# --- Through the application slice --------------------------------------------------------


def test_execute_task_records_validated_output_with_schema_and_payload_fields() -> None:
    """3D: a success with schema + structured fields records the Output via the validated path."""
    run = make_run()
    executor = StaticTaskExecutor(
        ExecutionResult(
            succeeded=True, output="draft v1", schema_ref="draft@v1", payload_fields={"body": "x"}
        )
    )
    task_id = execute_task(
        run,
        executor,
        workflow_step_ref=STEP_REF,
        agent_ref=AGENT_REF,
        task_input=TASK_INPUT,
        schema_binding=SchemaBinding("draft@v1", _active_schema()),
    )
    output = run.task(task_id).output
    assert output is not None
    assert output.payload == "draft v1"
    assert output.schema_ref == "draft@v1"
    assert output.status is OutputStatus.VALID


def test_execute_task_records_invalid_output_when_required_fields_missing() -> None:
    """3D: missing a required field -> the Output is recorded INVALID (Schema decides)."""
    run = make_run()
    executor = StaticTaskExecutor(
        ExecutionResult(succeeded=True, output="draft v1", schema_ref="draft@v1", payload_fields={})
    )
    task_id = execute_task(
        run,
        executor,
        workflow_step_ref=STEP_REF,
        agent_ref=AGENT_REF,
        task_input=TASK_INPUT,
        schema_binding=SchemaBinding("draft@v1", _active_schema()),
    )
    output = run.task(task_id).output
    assert output is not None
    assert output.status is OutputStatus.INVALID


def test_execute_task_records_the_authoritative_binding_not_the_executor_schema_ref() -> None:
    """ADR-0031: validation and Output labelling use the same trusted Schema binding."""
    run = make_run()
    executor = StaticTaskExecutor(
        ExecutionResult(
            succeeded=True,
            output="draft v1",
            schema_ref="untrusted-other-schema@v9",
            payload_fields={"body": "x"},
        )
    )
    task_id = execute_task(
        run,
        executor,
        workflow_step_ref=STEP_REF,
        agent_ref=AGENT_REF,
        task_input=TASK_INPUT,
        schema_binding=SchemaBinding("draft@v1", _active_schema()),
    )

    output = run.task(task_id).output
    assert output is not None
    assert output.schema_ref == "draft@v1"


def test_execute_task_records_no_output_without_a_schema() -> None:
    """3D: no schema (or no structured fields) -> no Output; there is no always-VALID fallback."""
    run = make_run()
    executor = StaticTaskExecutor(
        ExecutionResult(succeeded=True, output="draft v1", schema_ref="draft@v1")
    )
    task_id = execute_task(
        run, executor, workflow_step_ref=STEP_REF, agent_ref=AGENT_REF, task_input=TASK_INPUT
    )
    assert run.task(task_id).status is TaskStatus.SUCCEEDED
    assert run.task(task_id).output is None
