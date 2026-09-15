"""Minimal task-execution slice — prove a Run can execute one Task end to end.

This is the **smallest application-layer component** that drives a single Task through its
lifecycle inside a Run, coordinating everything **through the Run aggregate root** (never
bypassing it) and using an injected, deterministic executor instead of doing real work.

It validates the architecture, not a feature: a Run creates a Task, the Task transitions
through its lifecycle, Run coordinates it, domain events are recorded and the aggregate
invariants hold (including "a Run cannot complete with a non-terminal Task").

A successful execution records the Task's domain ``Output`` **only via the unified validated path**
(3D, evaluation-ownership Variant A): when a ``schema`` is supplied and the result carries
``output`` + ``schema_ref`` + structured ``payload_fields``, the application invokes
``Schema.validate`` (Schema = authority) and the pure sink ``Run.record_output`` persists the
VALID/INVALID verdict. There is no legacy always-VALID path. Skill preprocessing is an executor
decorator outside this slice (`ADR-0027`), so this module remains unaware of it. Still deliberately
**out of scope**: QA / Human Approval; providers; adapters; queues; Tools; and infrastructure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.application.schema_validation import validate_and_record_output
from omemo_content_factory.domain.run import Actor, Run
from omemo_content_factory.domain.schema import Schema
from omemo_content_factory.domain.task import TaskId, TaskStatus


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """What a task executor produced for one Task (application-layer, not a domain Output).

    ``succeeded`` decides the Task's terminal outcome; ``output`` carries the produced content
    on success and ``schema_ref`` the (opaque) contract it conforms to — together they are
    recorded as the Task's domain ``Output``; ``failure_reason`` explains a failure and is
    routed to the Task's FAILED reason. ``payload_fields`` optionally carries the **structured**
    fields of the result for later Schema validation (Slice 3); it is **not consumed by execution
    here** — it is data only until the validated path is wired (3C/3D).
    """

    succeeded: bool
    output: str | None = None
    schema_ref: str | None = None
    failure_reason: str | None = None
    payload_fields: Mapping[str, str] | None = None


class TaskExecutor(Protocol):
    """Performs a Task's work and reports the outcome — a plain execution abstraction.

    A single, explicit, injected dependency (PROJECT.md §6) that decouples task orchestration
    from *how* the work is done. The minimal slice supplies a deterministic fake; any concrete
    executor implements the same contract without changing this orchestration (Open/Closed,
    PROJECT.md §4.11). It is intentionally just an execution port, not tied to any particular
    kind of worker (in particular, it is not an Agent).
    """

    def execute(self, task_input: str) -> ExecutionResult: ...


def execute_task(
    run: Run,
    executor: TaskExecutor,
    *,
    workflow_step_ref: str,
    agent_ref: str,
    task_input: str,
    schema: Schema | None = None,
) -> TaskId:
    """Open and run one Task inside ``run``, driving it to a terminal outcome.

    Acts in the Content Director role: every Task operation goes through the Run aggregate root,
    so Run coordinates the Task, records the domain events and keeps its invariants. A successful
    execution ends the Task in ``SUCCEEDED``; a failed one in ``FAILED`` carrying the reason.

    Output finalization (3D, evaluation-ownership Variant A): an Output is recorded **only** via the
    unified validated path — when the result carries ``output``, ``schema_ref`` and structured
    ``payload_fields`` **and** a ``schema`` is supplied. The application then **invokes**
    ``Schema.validate`` (Schema = authority) and the **pure sink** ``Run.record_output`` persists
    the VALID/INVALID verdict. There is no legacy always-VALID recording path: with no ``schema``
    (or no structured fields) no Output is produced. Returns the new Task's id.

    The composition of :func:`start_task` and :func:`finish_task`; a caller that commits the Run
    between the two (the Content Director with a store, ADR-0026 §2) uses them directly.
    """
    task_id = start_task(
        run, workflow_step_ref=workflow_step_ref, agent_ref=agent_ref, task_input=task_input
    )
    finish_task(run, executor, task_id, schema=schema)
    return task_id


def start_task(run: Run, *, workflow_step_ref: str, agent_ref: str, task_input: str) -> TaskId:
    """Open one Task inside ``run`` and put it in ``RUNNING`` — everything before the executor call.

    Returns the new Task's id. The Task's work is done by :func:`finish_task` (ADR-0026 §2).
    """
    task_id = run.open_task(
        workflow_step_ref=workflow_step_ref,
        agent_ref=agent_ref,
        task_input=task_input,
        by=Actor.CONTENT_DIRECTOR,
    )
    run.transition_task(task_id, TaskStatus.RUNNING, by=Actor.CONTENT_DIRECTOR)
    return task_id


def finish_task(
    run: Run, executor: TaskExecutor, task_id: TaskId, *, schema: Schema | None = None
) -> None:
    """Run a ``RUNNING`` Task's executor on the Task's own input and record the outcome.

    Everything after :func:`start_task`: the executor is handed the input stored on the Task (so a
    resumed Task gets exactly what it was opened with, ADR-0026 §3), then the Task ends in
    ``SUCCEEDED`` (with its Output, via the validated path) or ``FAILED`` with the reason.
    """
    result = executor.execute(run.task(task_id).task_input)
    if not result.succeeded:
        run.transition_task(
            task_id,
            TaskStatus.FAILED,
            by=Actor.CONTENT_DIRECTOR,
            reason=result.failure_reason,
        )
        return
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=Actor.CONTENT_DIRECTOR)
    if (
        schema is not None
        and result.output is not None
        and result.schema_ref is not None
        and result.payload_fields is not None
    ):
        validate_and_record_output(
            run,
            task_id,
            schema=schema,
            payload_fields=result.payload_fields,
            payload=result.output,
            schema_ref=result.schema_ref,
            by=Actor.CONTENT_DIRECTOR,
        )
