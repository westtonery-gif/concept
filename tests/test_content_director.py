"""Integration tests for the minimal ContentDirector (``application.content_director``).

These verify that the coordinator sequences the existing domain into one whole workflow:
multiple Tasks are opened and executed through the Run root, and the Run is finalised to the
correct terminal state for the outcome. They do **not** re-test the Run/Task state machines or
``execute_task`` itself (covered by the run / task / task-execution test modules).
Deterministic fake executors stand in for real work; no LLM, network or infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass

from omemo_content_factory.application.content_director import ContentDirector, TaskRequest
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.domain.run import Run, RunStatus
from omemo_content_factory.domain.task import TaskCreated, TaskStatus

WORKFLOW_TASKS: list[TaskRequest] = [
    TaskRequest("step-research", "researcher@v1", "brief: benefits of magnesium"),
    TaskRequest("step-write", "writer@v1", "structure from research"),
    TaskRequest("step-edit", "editor@v1", "draft from writer"),
]


@dataclass(frozen=True, slots=True)
class SucceedingExecutor:
    """Deterministic executor: every Task succeeds, echoing its input into the output."""

    def execute(self, task_input: str) -> ExecutionResult:
        return ExecutionResult(succeeded=True, output=f"[generated] {task_input}")


@dataclass(frozen=True, slots=True)
class ExecutorFailingOn:
    """Deterministic executor that fails for one specific input and succeeds otherwise."""

    failing_input: str

    def execute(self, task_input: str) -> ExecutionResult:
        if task_input == self.failing_input:
            return ExecutionResult(succeeded=False, failure_reason=f"cannot process: {task_input}")
        return ExecutionResult(succeeded=True, output=f"[generated] {task_input}")


def run_demo_workflow(director: ContentDirector) -> Run:
    """Create a Run externally, orchestrate it through the director, and return it."""
    run = Run.create(
        run_id="run-wf-0001",
        content_brief_ref="brief-0001",
        workflow_version_ref="article@v1",
    )
    director.execute(run, WORKFLOW_TASKS)
    return run


def test_workflow_with_all_tasks_succeeding_completes_the_run() -> None:
    """Every Task succeeds -> the Run is driven through the gates to COMPLETED."""
    director = ContentDirector(SucceedingExecutor())
    run = run_demo_workflow(director)
    assert run.status is RunStatus.COMPLETED
    assert [view.status for view in run.tasks] == [TaskStatus.SUCCEEDED] * len(WORKFLOW_TASKS)


def test_workflow_with_a_failing_task_routes_the_run_to_failed() -> None:
    """A failed Task stops the sequential plan before any downstream Task is opened."""
    director = ContentDirector(ExecutorFailingOn("structure from research"))
    run = run_demo_workflow(director)
    assert run.status is RunStatus.FAILED
    assert run.failure_reason == "one or more tasks failed"
    assert [task.workflow_step_ref for task in run.tasks] == ["step-research", "step-write"]
    assert [task.status for task in run.tasks] == [TaskStatus.SUCCEEDED, TaskStatus.FAILED]


def test_workflow_opens_one_task_per_request_preserving_its_references() -> None:
    """The coordinator opens exactly one Task per request, carrying each request's input."""
    director = ContentDirector(SucceedingExecutor())
    run = run_demo_workflow(director)
    opened = [(view.workflow_step_ref, view.agent_ref, view.task_input) for view in run.tasks]
    assert opened == [
        (request.workflow_step_ref, request.agent_ref, request.task_input)
        for request in WORKFLOW_TASKS
    ]
    created = [event for event in run.events if isinstance(event, TaskCreated)]
    assert len(created) == len(WORKFLOW_TASKS)
