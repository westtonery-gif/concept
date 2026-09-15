"""Tests for the observability-only layer (application/schema_observability.py).

Verifies that VALID and INVALID decisions are traced, and that observation never changes the
behaviour of the validated wiring.
"""

from __future__ import annotations

from omemo_content_factory.application.schema_observability import InMemoryValidationLog
from omemo_content_factory.application.schema_validation import (
    SchemaBinding,
    validate_and_record_output,
)
from omemo_content_factory.domain.output import OutputStatus
from omemo_content_factory.domain.run import Actor, Run
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.task import TaskId, TaskStatus

_CD = Actor.CONTENT_DIRECTOR


def _succeeded_task(run: Run) -> TaskId:
    task_id = run.open_task(
        workflow_step_ref="research", agent_ref="researcher@v1", task_input="brief", by=_CD
    )
    run.transition_task(task_id, TaskStatus.RUNNING, by=_CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=_CD)
    return task_id


def _run() -> Run:
    return Run.create(run_id="run-1", content_brief_ref="brief-1", workflow_version_ref="wf@v1")


def _active_schema() -> Schema:
    schema = Schema.create(
        schema_id="research-notes",
        version=SchemaVersion(1),
        description="research notes contract",
        required_fields=["facts", "structure"],
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


def test_valid_decision_is_traced() -> None:
    run, schema, log = _run(), _active_schema(), InMemoryValidationLog()
    task_id = _succeeded_task(run)

    output_id = validate_and_record_output(
        run,
        task_id,
        schema_binding=SchemaBinding("research-notes@1", schema),
        payload_fields={"facts": "a", "structure": "b"},
        payload="content",
        observer=log,
    )

    assert output_id is not None  # behaviour unchanged
    assert len(log.events) == 1
    event = log.events[0]
    assert event.schema_id == "research-notes"
    assert event.schema_version == 1
    assert event.is_valid is True
    assert event.missing_fields == ()
    assert event.timestamp and event.payload_signature


def test_invalid_decision_is_traced_and_recorded_invalid() -> None:
    run, schema, log = _run(), _active_schema(), InMemoryValidationLog()
    task_id = _succeeded_task(run)

    output_id = validate_and_record_output(
        run,
        task_id,
        schema_binding=SchemaBinding("research-notes@1", schema),
        payload_fields={"facts": "a"},  # missing "structure"
        payload="content",
        observer=log,
    )

    assert output_id is not None
    output = run.task(task_id).output
    assert output is not None and output.status is OutputStatus.INVALID  # recorded, not gated
    assert len(log.events) == 1
    assert log.events[0].is_valid is False
    assert log.events[0].missing_fields == ("structure",)


def test_observation_is_optional_and_behaviour_identical_without_observer() -> None:
    run, schema = _run(), _active_schema()
    task_id = _succeeded_task(run)

    output_id = validate_and_record_output(
        run,
        task_id,
        schema_binding=SchemaBinding("research-notes@1", schema),
        payload_fields={"facts": "a", "structure": "b"},
        payload="content",
    )

    assert output_id is not None
    assert run.task(task_id).output is not None


def test_observer_error_does_not_affect_behaviour() -> None:
    class _Broken:
        def on_validation_result(self, event: object) -> None:
            raise RuntimeError("sink down")

    run, schema = _run(), _active_schema()
    task_id = _succeeded_task(run)

    output_id = validate_and_record_output(
        run,
        task_id,
        schema_binding=SchemaBinding("research-notes@1", schema),
        payload_fields={"facts": "a", "structure": "b"},
        payload="content",
        observer=_Broken(),
    )

    assert output_id is not None  # broken observer swallowed; flow unaffected
