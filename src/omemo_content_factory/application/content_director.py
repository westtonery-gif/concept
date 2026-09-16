"""Minimal ContentDirector — prove the existing domain runs a whole workflow.

A small, deterministic coordinator that sequences the **already existing** public operations
of the Run aggregate and the ``execute_task`` slice into one end-to-end workflow. It exists to
demonstrate that Run + Task + ``execute_task`` already function as a single system.

It is intentionally **not**: an intelligent agent, a production orchestrator, or the future
Content Director / Workflow Engine of ARCHITECTURE.md §4-5. It adds no new domain concepts,
never bypasses the Run aggregate root, and changes nothing in Run or Task. The only adapter it
knows is the ``RunStore`` Protocol, and only when one is injected.

The work itself is delegated to an injected :class:`TaskExecutor` (a deterministic fake in
tests and in the demo). When a Task succeeds with structured output, its ``Output`` is recorded
through the Run and an ``Artifact`` is created from it (provenance ``Task -> Output -> Artifact``).

QA gate (ADR-0018 §7): when a QA evaluator is injected, the ``WAITING_QA`` step becomes real — the
final step's Artifact is made a ``CANDIDATE`` and evaluated (``evaluate_artifact``). Only a
``PASSED`` verdict lets the Run go on; a risk verdict is **fail closed**: the Run stops at the
Approval Gate (``WAITING_HUMAN``) with a Human Review opened for escalation, never ``COMPLETED``.
Without an evaluator the flow is unchanged. The Evaluation names the evaluator's role, and the
evaluator's model calls are recorded against it — and committed even when it then fails (ADR-0036).

Rework routing (ADR-0032): a QA risk still escalates to a Human Review. When its latest decision is
``CHANGES_REQUESTED``, :meth:`ContentDirector.resume` re-executes only the current candidate's
producer with the reviewed content and feedback, then creates a new Artifact version from the
validated Output. The route is bounded by the Run's rework policy and starts fresh QA/review gates.

Contract errors (ADR-0033): an Output its Schema ruled ``INVALID`` stays recorded for audit, but it
never becomes an Artifact or a new version and is never handed to the next step; the plan stops
there and the Run is routed to ``FAILED``.

Storage (ADR-0026/0032): when a ``RunStore`` is injected, the Run is saved after every
orchestration step — each commit that precedes an external call is taken before the call — so a
stored Run never holds a half-recorded step. :meth:`ContentDirector.resume` continues a Run brought
back from the store from its own state: committed work is reused, an uncommitted call is made again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.application.qa_evaluation import (
    QA_KIND,
    ArtifactEvaluator,
    MeasuredEvaluatorError,
    record_verdict,
)
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import (
    TaskExecutor,
    finish_task,
    start_task,
)
from omemo_content_factory.domain.artifact import ArtifactId, ArtifactStatus, ArtifactView
from omemo_content_factory.domain.evaluation import EvaluationStatus, EvaluationView
from omemo_content_factory.domain.human_review import HumanReviewView, ReviewStatus
from omemo_content_factory.domain.output import OutputStatus
from omemo_content_factory.domain.run import (
    Actor,
    ReworkLimitExceededError,
    Run,
    RunStatus,
)
from omemo_content_factory.domain.task import TaskRetryLimitExceededError, TaskStatus
from omemo_content_factory.domain.workflow import Workflow

_CD = Actor.CONTENT_DIRECTOR

NO_CANDIDATE_REASON = "QA gate: no candidate artifact to evaluate"
"""Failure reason when QA is wired but the final step produced no Artifact (ADR-0018 §7)."""

RESUME_LIMIT_REASON = "interrupted before its result was committed; no attempts left to resume it"
"""Failure reason of a Task found ``RUNNING`` on restart with no attempts left (ADR-0026 §3)."""

REWORK_LIMIT_REASON = "Rework requested but the configured iteration limit is exhausted"
"""Failure reason when a human-requested iteration exceeds ``ReworkPolicy`` (ADR-0032 §1)."""

REWORK_NO_OUTPUT_REASON = "Rework task succeeded without a validated Output"
"""Failure reason when re-execution cannot produce an Artifact version (ADR-0032 §3)."""

INVALID_OUTPUT_REASON = "Output violates its Schema contract"
"""Failure reason when a step's recorded Output was ruled ``INVALID`` (ADR-0033 §1)."""


class RunResumptionError(Exception):
    """The Run handed to ``resume`` is not a run of the given plan (ADR-0026 §3).

    An application error, not a ``DomainError``: no domain rule was broken — the orchestrator was
    handed a Run and a Task sequence that do not belong together. Raised before anything changes.
    """


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
    ``store`` optionally injects the ``RunStore`` every step is committed to (ADR-0026).
    """

    def __init__(
        self,
        executor: TaskExecutor | Mapping[str, TaskExecutor],
        schemas: Mapping[str, SchemaBinding] | None = None,
        qa: ArtifactEvaluator | None = None,
        store: RunStore | None = None,
    ) -> None:
        self._executor = executor
        self._schemas = schemas
        self._qa = qa
        self._store = store

    def execute(self, run: Run, tasks: Sequence[TaskRequest]) -> None:
        """Orchestrate an **already-created** Run through its full lifecycle.

        Creating the Run is the caller's responsibility (e.g. an entry point), in line with
        ARCHITECTURE.md §3.4 where the Content Director receives a *created* Run; this method
        only orchestrates. The Run must be freshly created (in ``CREATED``); a Run in any other
        state is refused by its own transition guard — continuing one is :meth:`resume`.

        Steps (all via the public aggregate API, never bypassing the root):
        ``QUEUED`` -> ``RUNNING`` -> for each request, the role's executor runs the Task
        (``execute_task``) and, when it produced an Output, an Artifact is created from it
        (provenance ``Task -> Output -> Artifact``). Structured data flows between steps: a Task's
        Output becomes the next Task's input (ARCHITECTURE.md §4), so the first step gets the
        brief (its ``task_input``) and each later step gets the previous Output. If every Task
        succeeded, the Run proceeds ``WAITING_QA`` -> (QA gate, when wired) -> ``WAITING_HUMAN``
        -> ``COMPLETED``; otherwise it is routed to ``FAILED``. The Run is mutated in place and,
        with a store, committed after every step (ADR-0026 §2).
        """
        run.transition(RunStatus.QUEUED, by=_CD)
        self._commit(run)
        self._drive(run, tasks)

    def resume(self, run: Run, tasks: Sequence[TaskRequest]) -> None:
        """Continue ``run`` — typically one brought back by ``RunStore.load`` — from its state.

        Progress is read from the Run itself (ADR-0026 §3): the i-th Task belongs to the i-th
        request; a terminal Task is not run again (a ``SUCCEEDED`` one hands its Output on), a
        ``RUNNING`` one is retried on its stored input, a missing one is started. The QA gate
        re-uses a ``PENDING`` Evaluation and routes a recorded verdict without asking again. A Run
        waiting for a human is left alone unless the current candidate's latest Review is
        ``CHANGES_REQUESTED``; that decision starts one bounded, resumable rework of the
        candidate's producer (ADR-0032). A terminal Run is left alone. A Run whose Tasks do not
        match ``tasks`` raises :class:`RunResumptionError` before anything changes. A freshly
        created Run is simply executed.
        """
        if run.status is RunStatus.RUNNING:
            self._ensure_matches(run, tasks)
        if run.status is RunStatus.CREATED:
            self.execute(run, tasks)
            return
        self._drive(run, tasks)

    def execute_workflow(self, run: Run, workflow: Workflow, *, brief: str) -> None:
        """Resolve a Workflow into the Task sequence (strict list order) and run it (ADR-0009 §8).

        Positional, list-order expansion only: no DAG, no scheduling, no optimisation, no use of
        ``depends_on``. Data flow (output -> next input) and Schema resolution stay in the unchanged
        execution core; the Workflow's ``schema_ref`` is declarative and is **not** threaded into
        execution. Delegates to the existing ``execute`` without changing it.
        """
        self.execute(run, self.expand(workflow, brief=brief))

    def resume_workflow(self, run: Run, workflow: Workflow, *, brief: str) -> None:
        """:meth:`resume` for a Workflow, expanded as :meth:`execute_workflow` expands it."""
        self.resume(run, self.expand(workflow, brief=brief))

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

    # --- The lifecycle, driven from whatever state the Run is in -------------------------

    def _drive(self, run: Run, tasks: Sequence[TaskRequest]) -> None:
        """Advance ``run`` from its current state as far as the Director can take it."""
        if self._changes_requested(run):
            self._begin_rework(run, tasks)
            if run.status is RunStatus.FAILED:
                return
        if run.status is RunStatus.QUEUED:
            run.transition(RunStatus.RUNNING, by=_CD)
            self._commit(run)
        if not self._drive_running(run, tasks):
            return
        if run.status is RunStatus.WAITING_QA:
            if self._qa is None and run.rework_count:
                return
            if self._qa is not None and not self._pass_qa_gate(run, self._qa):
                return
            run.transition(RunStatus.WAITING_HUMAN, by=_CD)
            self._commit(run)
        self._route_human_gate(run)

    def _drive_running(self, run: Run, tasks: Sequence[TaskRequest]) -> bool:
        """Drive either the original plan or its current rework; whether routing may continue."""
        if run.status is not RunStatus.RUNNING:
            return True
        if run.rework_count:
            if not self._run_rework(run, tasks):
                return False
        else:
            self._run_steps(run, tasks)
        if not self._all_tasks_succeeded(run):
            run.transition(RunStatus.FAILED, by=_CD, reason="one or more tasks failed")
            self._commit(run)
            return False
        if self._has_invalid_output(run):
            run.transition(RunStatus.FAILED, by=_CD, reason=INVALID_OUTPUT_REASON)
            self._commit(run)
            return False
        run.transition(RunStatus.WAITING_QA, by=_CD)
        self._commit(run)
        return True

    def _route_human_gate(self, run: Run) -> None:
        """Open the successor's fresh review, or preserve the legacy no-QA completion route."""
        if run.status is not RunStatus.WAITING_HUMAN:
            return
        if run.rework_count:
            candidate = self._final_candidate(run)
            if candidate is not None and self._latest_review(run, candidate) is None:
                run.open_human_review(candidate, by=_CD)
                self._commit(run)
            return
        if not run.human_reviews:
            run.transition(RunStatus.COMPLETED, by=_CD)
            self._commit(run)

    # --- Rework -------------------------------------------------------------------------

    def _begin_rework(self, run: Run, tasks: Sequence[TaskRequest]) -> None:
        """Enter one human-requested rework iteration, or fail at the configured bound."""
        self._ensure_matches(run, tasks)
        try:
            run.transition(RunStatus.RUNNING, by=_CD)
        except ReworkLimitExceededError:
            run.transition(RunStatus.FAILED, by=_CD, reason=REWORK_LIMIT_REASON)
        self._commit(run)

    def _run_rework(self, run: Run, tasks: Sequence[TaskRequest]) -> bool:
        """Finish the current rework iteration and create/reuse its Artifact successor."""
        self._ensure_matches(run, tasks)
        if not tasks:
            raise RunResumptionError(f"run {run.run_id} cannot rework an empty plan")
        if len(run.tasks) < len(tasks):
            raise RunResumptionError(
                f"run {run.run_id} entered rework with only {len(run.tasks)} of "
                f"{len(tasks)} original Tasks"
            )
        task_index = len(tasks) + run.rework_count - 1
        if task_index < len(run.tasks):
            task_id = run.tasks[task_index].task_id
            self._continue_rework(run, task_id, tasks[-1])
        elif task_index == len(run.tasks):
            source, review = self._rework_source(run)
            task_id = start_task(
                run,
                workflow_step_ref=tasks[-1].workflow_step_ref,
                agent_ref=tasks[-1].agent_ref,
                task_input=self._rework_input(run, source, review),
            )
            self._commit(run)
            self._finish_rework(run, task_id, tasks[-1], source.artifact_id)
        else:
            raise RunResumptionError(
                f"run {run.run_id} is missing the Task of an earlier rework iteration"
            )
        task = run.task(task_id)
        if task.status is not TaskStatus.SUCCEEDED:
            run.transition(RunStatus.FAILED, by=_CD, reason="one or more tasks failed")
            self._commit(run)
            return False
        if task.output is None or task.output.status is not OutputStatus.VALID:
            run.transition(RunStatus.FAILED, by=_CD, reason=REWORK_NO_OUTPUT_REASON)
            self._commit(run)
            return False
        return True

    def _continue_rework(self, run: Run, task_id: str, request: TaskRequest) -> None:
        """Resume an appended rework Task without duplicating a committed successor."""
        task = run.task(task_id)
        if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            try:
                run.transition_task(task_id, TaskStatus.RUNNING, by=_CD)
            except TaskRetryLimitExceededError:
                run.transition_task(task_id, TaskStatus.FAILED, by=_CD, reason=RESUME_LIMIT_REASON)
                self._commit(run)
                return
            self._commit(run)
            source, _ = self._rework_source(run)
            self._finish_rework(run, task_id, request, source.artifact_id)
            return
        output = task.output
        if (
            output is not None
            and output.status is OutputStatus.VALID
            and self._artifact_of(run, output.output_id) is None
        ):
            source, _ = self._rework_source(run)
            run.create_artifact_version(source.artifact_id, output.output_id, by=_CD)
            self._commit(run)

    def _finish_rework(
        self, run: Run, task_id: str, request: TaskRequest, previous_artifact_id: ArtifactId
    ) -> None:
        """Call the producer and atomically commit its outcome with the new Artifact version."""
        finish_task(
            run,
            self._resolve(request.agent_ref),
            task_id,
            schema_binding=self._resolve_schema(request.agent_ref),
        )
        output = run.task(task_id).output
        if output is not None and output.status is OutputStatus.VALID:
            run.create_artifact_version(previous_artifact_id, output.output_id, by=_CD)
        self._commit(run)

    def _rework_source(self, run: Run) -> tuple[ArtifactView, HumanReviewView]:
        """Return the live candidate and its latest ``CHANGES_REQUESTED`` Review."""
        candidates = [view for view in run.artifacts if view.status is ArtifactStatus.CANDIDATE]
        if not candidates:
            raise RunResumptionError(f"run {run.run_id} has no live candidate to rework")
        source = candidates[-1]
        review = self._latest_review(run, source.artifact_id)
        if review is None or review.status is not ReviewStatus.CHANGES_REQUESTED:
            raise RunResumptionError(
                f"run {run.run_id} has no CHANGES_REQUESTED review for {source.artifact_id}"
            )
        return source, review

    def _rework_input(self, run: Run, source: ArtifactView, review: HumanReviewView) -> str:
        """Build the canonical, persisted JSON context of one rework call (ADR-0032 §2)."""
        evaluation = self._latest_qa(run, source.artifact_id)
        return json.dumps(
            {
                "artifact": {
                    "content": source.content,
                    "ref": source.artifact_id,
                    "version": source.version,
                },
                "human_instructions": review.reason,
                "qa_flags": [] if evaluation is None else list(evaluation.flags),
                "rework_iteration": run.rework_count,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def _changes_requested(cls, run: Run) -> bool:
        """Whether the current candidate's latest review requests a new iteration."""
        if run.status is not RunStatus.WAITING_HUMAN:
            return False
        candidate = cls._final_candidate(run)
        if candidate is None:
            return False
        review = cls._latest_review(run, candidate)
        return review is not None and review.status is ReviewStatus.CHANGES_REQUESTED

    def _run_steps(self, run: Run, tasks: Sequence[TaskRequest]) -> None:
        """Run requests in order, chaining Output and stopping at the first failed step.

        Commits once when a Task is started (before its executor is called) and once when its
        outcome, Output and Artifact are all recorded (ADR-0026 §2). A non-successful Task stops
        the sequential plan before any downstream request is opened or resumed (ADR-0031), and so
        does a successful one whose Output was ruled ``INVALID`` (ADR-0033 §1).
        """
        existing = run.tasks
        chained_input: str | None = None
        for index, request in enumerate(tasks):
            if index < len(existing):
                task_id = existing[index].task_id
                self._continue(run, task_id, request)
            else:
                task_input = request.task_input if chained_input is None else chained_input
                task_id = start_task(
                    run,
                    workflow_step_ref=request.workflow_step_ref,
                    agent_ref=request.agent_ref,
                    task_input=task_input,
                )
                self._commit(run)
                self._finish(run, task_id, request)
            task = run.task(task_id)
            output = task.output
            if task.status is not TaskStatus.SUCCEEDED or (
                output is not None and output.status is OutputStatus.INVALID
            ):
                break
            if output is not None:
                chained_input = output.payload

    def _continue(self, run: Run, task_id: str, request: TaskRequest) -> None:
        """Bring a Task the Run already owns to its outcome without redoing committed work.

        A terminal Task is not run again; it only gets the Artifact of its Output if that is
        missing. ``PENDING`` is started and ``RUNNING`` retried — its executor's answer was never
        committed — unless its attempts are exhausted: then it is failed (ADR-0026 §3, §4).
        """
        if run.task(task_id).status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            if self._add_artifact(run, task_id, request):
                self._commit(run)
            return
        try:
            run.transition_task(task_id, TaskStatus.RUNNING, by=_CD)
        except TaskRetryLimitExceededError:
            run.transition_task(task_id, TaskStatus.FAILED, by=_CD, reason=RESUME_LIMIT_REASON)
            self._commit(run)
            return
        self._commit(run)
        self._finish(run, task_id, request)

    def _finish(self, run: Run, task_id: str, request: TaskRequest) -> None:
        """Call the step's executor; record the outcome and its Artifact; commit them together."""
        finish_task(
            run,
            self._resolve(request.agent_ref),
            task_id,
            schema_binding=self._resolve_schema(request.agent_ref),
        )
        self._add_artifact(run, task_id, request)
        self._commit(run)

    def _add_artifact(self, run: Run, task_id: str, request: TaskRequest) -> bool:
        """Create the Artifact of the Task's VALID Output if none exists yet; whether made.

        An ``INVALID`` Output never becomes an Artifact (ADR-0033 §1).
        """
        output = run.task(task_id).output
        if (
            output is None
            or output.status is OutputStatus.INVALID
            or self._artifact_of(run, output.output_id) is not None
        ):
            return False
        run.create_artifact(output.output_id, kind=request.artifact_kind, by=_CD)
        return True

    # --- QA gate ------------------------------------------------------------------------

    def _pass_qa_gate(self, run: Run, qa: ArtifactEvaluator) -> bool:
        """Run the fail-closed QA gate on the final Artifact; ``True`` only on ``PASSED``.

        No candidate -> the Run is routed to ``FAILED`` (nothing to evaluate is not a pass). A risk
        verdict (``FLAGGED`` / ``FAILED``) -> forced escalation (PROJECT.md §12): the Run stops at
        ``WAITING_HUMAN`` with a Human Review opened on the candidate; the Run's approval gate keeps
        the Artifact from being approved (ADR-0018 §5, §7). A ``PENDING`` Evaluation a restart left
        behind is finished instead of opening a second one; a recorded verdict is routed as it is.
        An evaluator exception leaves the Run at ``WAITING_QA`` with the Evaluation ``PENDING`` and
        propagates; :meth:`resume` asks again (ADR-0038 §1).
        """
        candidate = self._final_candidate(run)
        if candidate is None:
            run.transition(RunStatus.FAILED, by=_CD, reason=NO_CANDIDATE_REASON)
            self._commit(run)
            return False
        evaluation = self._latest_qa(run, candidate)
        if evaluation is None:
            if run.artifact(candidate).status is ArtifactStatus.DRAFT:
                run.transition_artifact(candidate, ArtifactStatus.CANDIDATE, by=_CD)
            evaluation = run.evaluation(
                run.open_evaluation(candidate, kind=QA_KIND, by=_CD, evaluator_ref=qa.evaluator_ref)
            )
            self._commit(run)
        if evaluation.status is EvaluationStatus.PENDING:
            try:
                record_verdict(run, qa, evaluation.evaluation_id)
            except MeasuredEvaluatorError:
                self._commit(run)  # the failed evaluator's recorded calls are facts (ADR-0036 §3)
                raise
            self._commit(run)
        if run.evaluation(evaluation.evaluation_id).status is EvaluationStatus.PASSED:
            return True
        run.transition(RunStatus.WAITING_HUMAN, by=_CD)
        run.open_human_review(candidate, by=_CD)
        self._commit(run)
        return False

    @staticmethod
    def _final_candidate(run: Run) -> ArtifactId | None:
        """The Artifact of the final step's Output, or ``None`` when that step produced none."""
        if not run.tasks:
            return None
        output = run.tasks[-1].output
        return None if output is None else ContentDirector._artifact_of(run, output.output_id)

    @staticmethod
    def _latest_qa(run: Run, candidate: ArtifactId) -> EvaluationView | None:
        """The latest QA Evaluation of ``candidate`` (evaluations are kept in opening order)."""
        matching = [
            view
            for view in run.evaluations
            if view.artifact_ref == candidate and view.kind == QA_KIND
        ]
        return matching[-1] if matching else None

    @staticmethod
    def _latest_review(run: Run, candidate: ArtifactId) -> HumanReviewView | None:
        """The latest Human Review of ``candidate`` (reviews are kept in opening order)."""
        matching = [view for view in run.human_reviews if view.artifact_ref == candidate]
        return matching[-1] if matching else None

    # --- Helpers ------------------------------------------------------------------------

    def _commit(self, run: Run) -> None:
        """Save the Run's whole current truth, when a store is wired (ADR-0026 §1)."""
        if self._store is not None:
            self._store.save(run)

    def _resolve(self, agent_ref: str) -> TaskExecutor:
        """Pick the executor for a role: the per-role mapping entry, or the single executor."""
        executor = self._executor
        if isinstance(executor, Mapping):
            return executor[agent_ref]
        return executor

    def _resolve_schema(self, agent_ref: str) -> SchemaBinding | None:
        """Select the role's Schema binding from the injected map (ADR-0013 §8; ADR-0031).

        Returns ``None`` when no schema map is wired — then ``finish_task`` records no Output (the
        validated finalization is the only path; there is no always-VALID fallback).
        """
        if self._schemas is None:
            return None
        return self._schemas.get(agent_ref)

    @staticmethod
    def _ensure_matches(run: Run, tasks: Sequence[TaskRequest]) -> None:
        """Refuse a Run whose base/rework Tasks do not match the supplied plan (ADR-0032 §4)."""
        views = run.tasks
        if len(views) > len(tasks) + run.rework_count:
            raise RunResumptionError(
                f"run {run.run_id} has {len(views)} tasks for {len(tasks)} plan steps and "
                f"{run.rework_count} rework iterations"
            )
        for view, request in zip(views[: len(tasks)], tasks, strict=False):
            if (view.workflow_step_ref, view.agent_ref) != (
                request.workflow_step_ref,
                request.agent_ref,
            ):
                raise RunResumptionError(
                    f"task {view.task_id} is step '{view.workflow_step_ref}' of "
                    f"'{view.agent_ref}', but the plan has step '{request.workflow_step_ref}' "
                    f"of '{request.agent_ref}' there"
                )
        extras = views[len(tasks) :]
        if extras and not tasks:
            raise RunResumptionError(f"run {run.run_id} has rework Tasks for an empty plan")
        if tasks:
            final = tasks[-1]
            for view in extras:
                if (view.workflow_step_ref, view.agent_ref) != (
                    final.workflow_step_ref,
                    final.agent_ref,
                ):
                    raise RunResumptionError(
                        f"rework task {view.task_id} is step '{view.workflow_step_ref}' of "
                        f"'{view.agent_ref}', but the final producer is step "
                        f"'{final.workflow_step_ref}' of '{final.agent_ref}'"
                    )

    @staticmethod
    def _artifact_of(run: Run, output_id: str) -> ArtifactId | None:
        """The Artifact made from ``output_id``, if any (Output -> Artifact is 1:1)."""
        for view in run.artifacts:
            if view.output_ref == output_id:
                return view.artifact_id
        return None

    @staticmethod
    def _has_invalid_output(run: Run) -> bool:
        """Whether any Task of ``run`` recorded an Output its Schema ruled ``INVALID``."""
        return any(
            view.output is not None and view.output.status is OutputStatus.INVALID
            for view in run.tasks
        )

    @staticmethod
    def _all_tasks_succeeded(run: Run) -> bool:
        """Whether every Task owned by ``run`` reached ``SUCCEEDED`` (read-only via the root)."""
        return all(view.status is TaskStatus.SUCCEEDED for view in run.tasks)
