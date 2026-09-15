"""Minimal ContentDirector — prove the existing domain runs a whole workflow.

A small, deterministic coordinator that sequences the **already existing** public operations
of the Run aggregate and the ``execute_task`` slice into one end-to-end workflow. It exists to
demonstrate that Run + Task + ``execute_task`` already function as a single system.

It is intentionally **not**: an intelligent agent, a production orchestrator, or the future
Content Director / Workflow Engine of ARCHITECTURE.md §4-5. It adds no new domain concepts,
introduces no infrastructure (no LLM, network, adapters, queues), never bypasses the Run
aggregate root, and changes nothing in Run or Task.

The work itself is delegated to an injected :class:`TaskExecutor` (a deterministic fake in
tests and in the demo). When a Task succeeds with structured output, its ``Output`` is recorded
through the Run and an ``Artifact`` is created from it (provenance ``Task -> Output -> Artifact``).

QA gate (ADR-0018 §7): when a QA evaluator is injected, the ``WAITING_QA`` step becomes real — the
final step's Artifact is made a ``CANDIDATE`` and evaluated (``evaluate_artifact``). Only a
``PASSED`` verdict lets the Run go on; a risk verdict is **fail closed**: the Run stops at the
Approval Gate (``WAITING_HUMAN``) with a Human Review opened for escalation, never ``COMPLETED``.
Without an evaluator the flow is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from omemo_content_factory.application.qa_evaluation import ArtifactEvaluator, evaluate_artifact
from omemo_content_factory.application.task_execution import TaskExecutor, execute_task
from omemo_content_factory.domain.artifact import ArtifactId, ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.run import Actor, Run, RunStatus
from omemo_content_factory.domain.schema import Schema
from omemo_content_factory.domain.task import TaskStatus
from omemo_content_factory.domain.workflow import Workflow

_CD = Actor.CONTENT_DIRECTOR

NO_CANDIDATE_REASON = "QA gate: no candidate artifact to evaluate"
"""Failure reason when QA is wired but the final step produced no Artifact (ADR-0018 §7)."""


@dataclass(frozen=True, slots=True)
class TaskRequest:
    """One task to run within a workflow: which step, which role, the input, the artifact kind.

    A plain application-level input describing a unit of work. It is **not** the domain
    ``Workflow Step`` entity (deliberately out of scope at this stage); it only carries the
    opaque references and input that ``Run.open_task`` already requires, plus the ``kind`` of
    Artifact the step's Output yields (``Run.create_artifact``; ADR-0006 §10).
    """

    workflow_step_ref: str
    agent_ref: str
    task_input: str
    artifact_kind: str = "draft"


class ContentDirector:
    """Sequences the existing domain operations into one workflow.

    The executor is the injected execution abstraction. It may be a **single** ``TaskExecutor``
    used for every step, or a **mapping** from ``agent_ref`` (role) to a ``TaskExecutor`` so each
    role runs with its own implementation/prompt — selecting which executor runs a step is
    orchestration (ARCHITECTURE.md §4: the Content Director "calls agents"). The executors
    themselves are unaware of one another; the role's prompt lives inside its executor, not here.
    ``qa`` optionally injects the QA evaluator that makes the ``WAITING_QA`` gate real (ADR-0018).
    """

    def __init__(
        self,
        executor: TaskExecutor | Mapping[str, TaskExecutor],
        schemas: Mapping[str, Schema] | None = None,
        qa: ArtifactEvaluator | None = None,
    ) -> None:
        self._executor = executor
        self._schemas = schemas
        self._qa = qa

    def execute(self, run: Run, tasks: Sequence[TaskRequest]) -> None:
        """Orchestrate an **already-created** Run through its full lifecycle.

        Creating the Run is the caller's responsibility (e.g. an entry point), in line with
        ARCHITECTURE.md §3.4 where the Content Director receives a *created* Run; this method
        only orchestrates. The Run is expected to be freshly created (in ``CREATED``).

        Steps (all via the public aggregate API, never bypassing the root):
        ``QUEUED`` -> ``RUNNING`` -> for each request, the role's executor runs the Task
        (``execute_task``) and, when it produced an Output, an Artifact is created from it
        (provenance ``Task -> Output -> Artifact``). Structured data flows between steps: a Task's
        Output becomes the next Task's input (ARCHITECTURE.md §4), so the first step gets the
        brief (its ``task_input``) and each later step gets the previous Output. If every Task
        succeeded, the Run proceeds ``WAITING_QA`` -> (QA gate, when wired) -> ``WAITING_HUMAN``
        -> ``COMPLETED``; otherwise it is routed to ``FAILED``. The Run is mutated in place.
        """
        run.transition(RunStatus.QUEUED, by=_CD)
        run.transition(RunStatus.RUNNING, by=_CD)
        chained_input: str | None = None
        candidate: ArtifactId | None = None
        for request in tasks:
            task_input = request.task_input if chained_input is None else chained_input
            task_id = execute_task(
                run,
                self._resolve(request.agent_ref),
                workflow_step_ref=request.workflow_step_ref,
                agent_ref=request.agent_ref,
                task_input=task_input,
                schema=self._resolve_schema(request.agent_ref),
            )
            output = run.task(task_id).output
            candidate = None
            if output is not None:
                candidate = run.create_artifact(
                    output.output_id, kind=request.artifact_kind, by=_CD
                )
                chained_input = output.payload
        if not self._all_tasks_succeeded(run):
            run.transition(RunStatus.FAILED, by=_CD, reason="one or more tasks failed")
            return
        run.transition(RunStatus.WAITING_QA, by=_CD)
        if self._qa is not None and not self._pass_qa_gate(run, self._qa, candidate):
            return
        run.transition(RunStatus.WAITING_HUMAN, by=_CD)
        run.transition(RunStatus.COMPLETED, by=_CD)

    def execute_workflow(self, run: Run, workflow: Workflow, *, brief: str) -> None:
        """Resolve a Workflow into the Task sequence (strict list order) and run it (ADR-0009 §8).

        Positional, list-order expansion only: no DAG, no scheduling, no optimisation, no use of
        ``depends_on``. Data flow (output -> next input) and Schema resolution stay in the unchanged
        execution core; the Workflow's ``schema_ref`` is declarative and is **not** threaded into
        execution. Delegates to the existing ``execute`` without changing it.
        """
        self.execute(run, self.expand(workflow, brief=brief))

    @staticmethod
    def expand(workflow: Workflow, *, brief: str) -> list[TaskRequest]:
        """Map ``Workflow.steps`` to a ``TaskRequest`` sequence in strict list order (ADR-0009 §8).

        Deterministic positional mapping: the i-th step becomes the i-th ``TaskRequest``
        (``workflow_step_ref`` = ``step_id``, role = ``agent_ref``). The first step receives the
        ``brief``; later steps receive the previous step's Output via the existing pipeline's
        chaining. ``depends_on``, ``task_type`` and ``schema_ref`` are declarative and are not used
        here — no ordering, no execution semantics.
        """
        return [
            TaskRequest(
                workflow_step_ref=step.step_id,
                agent_ref=step.agent_ref,
                task_input=brief if index == 0 else "",
            )
            for index, step in enumerate(workflow.steps)
        ]

    def _resolve(self, agent_ref: str) -> TaskExecutor:
        """Pick the executor for a role: the per-role mapping entry, or the single executor."""
        executor = self._executor
        if isinstance(executor, Mapping):
            return executor[agent_ref]
        return executor

    def _resolve_schema(self, agent_ref: str) -> Schema | None:
        """Select the role's Schema from the injected map (selection only; ADR-0013 §8).

        Returns ``None`` when no schema map is wired — then ``execute_task`` records no Output (the
        validated finalization is the only path; there is no always-VALID fallback).
        """
        if self._schemas is None:
            return None
        return self._schemas.get(agent_ref)

    @staticmethod
    def _pass_qa_gate(run: Run, qa: ArtifactEvaluator, candidate: ArtifactId | None) -> bool:
        """Run the fail-closed QA gate on the final Artifact; ``True`` only on ``PASSED``.

        No candidate -> the Run is routed to ``FAILED`` (nothing to evaluate is not a pass). A risk
        verdict (``FLAGGED`` / ``FAILED``) -> forced escalation (PROJECT.md §12): the Run stops at
        ``WAITING_HUMAN`` with a Human Review opened on the candidate; the Run's approval gate keeps
        the Artifact from being approved (ADR-0018 §5, §7).
        """
        if candidate is None:
            run.transition(RunStatus.FAILED, by=_CD, reason=NO_CANDIDATE_REASON)
            return False
        run.transition_artifact(candidate, ArtifactStatus.CANDIDATE, by=_CD)
        evaluation_id = evaluate_artifact(run, qa, candidate)
        if run.evaluation(evaluation_id).status is EvaluationStatus.PASSED:
            return True
        run.transition(RunStatus.WAITING_HUMAN, by=_CD)
        run.open_human_review(candidate, by=_CD)
        return False

    @staticmethod
    def _all_tasks_succeeded(run: Run) -> bool:
        """Whether every Task owned by ``run`` reached ``SUCCEEDED`` (read-only via the root)."""
        return all(view.status is TaskStatus.SUCCEEDED for view in run.tasks)
