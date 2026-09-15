"""Acceptance for resumable QA/Human rework routing (ADR-0032).

A QA risk is still escalated to a human. ``CHANGES_REQUESTED`` then re-executes only the current
candidate's producer, persists the exact feedback context on a new Task and creates the successor
through ``Run.create_artifact_version``. Fakes are deterministic; no LLM, network or sleep.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.application.content_director import (
    REWORK_LIMIT_REASON,
    REWORK_NO_OUTPUT_REASON,
    ContentDirector,
    RunResumptionError,
    TaskRequest,
)
from omemo_content_factory.application.qa_evaluation import EvaluationResult
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Actor, ReworkPolicy, Run, RunSnapshot, RunStatus
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion

CD = Actor.CONTENT_DIRECTOR
REVIEWER = Actor.HUMAN_REVIEWER
FLAGGED = EvaluationStatus.FLAGGED
PASSED = EvaluationStatus.PASSED

REQUESTS = [
    TaskRequest("research", "researcher@v1", "brief", artifact_kind="research"),
    TaskRequest("write", "writer@v1", "", artifact_kind="script"),
]


def _schema() -> Schema:
    """An ACTIVE Schema with no required fields, sufficient for routing acceptance."""
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=[]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


@dataclass
class ScriptedExecutor:
    """Produce the original two Outputs, then consume one scripted result per rework call."""

    rework_results: list[ExecutionResult]
    calls: list[str] = field(default_factory=list)

    def execute(self, task_input: str) -> ExecutionResult:
        self.calls.append(task_input)
        if task_input == "brief":
            return self._success("research")
        if task_input == "research":
            return self._success("script v1")
        return self.rework_results.pop(0)

    @staticmethod
    def _success(output: str) -> ExecutionResult:
        return ExecutionResult(
            succeeded=True, output=output, schema_ref="untrusted", payload_fields={}
        )


@dataclass
class SequenceEvaluator:
    """Return QA verdicts in order and retain every candidate content shown."""

    verdicts: list[EvaluationStatus]
    calls: list[str] = field(default_factory=list)
    evaluator_ref: str = "qa@v1"

    def evaluate(self, content: str) -> EvaluationResult:
        self.calls.append(content)
        verdict = self.verdicts.pop(0)
        flags = (f"risk in {content}",) if verdict is not PASSED else ()
        return EvaluationResult(verdict, flags)


class SnapshotStore:
    """In-memory whole-Run store used to prove the ADR-0032 commit boundaries."""

    def __init__(self) -> None:
        self.snapshots: list[RunSnapshot] = []

    def save(self, run: Run, /) -> None:
        self.snapshots.append(run.snapshot)

    def load(self, run_id: str, /) -> Run | None:
        if not self.snapshots or self.snapshots[-1].run_id != run_id:
            return None
        return Run.restore(self.snapshots[-1])


class CrashAfterSave:
    """Persist a snapshot, then simulate process death after a selected new save."""

    def __init__(self, inner: RunStore, crash_after: int) -> None:
        self._inner = inner
        self._crash_after = crash_after
        self._saves = 0

    def save(self, run: Run, /) -> None:
        self._inner.save(run)
        self._saves += 1
        if self._saves == self._crash_after:
            raise ProcessCrashError

    def load(self, run_id: str, /) -> Run | None:
        return self._inner.load(run_id)


class ProcessCrashError(Exception):
    """The process stops immediately at this persistence boundary."""


def _success(output: str) -> ExecutionResult:
    return ExecutionResult(succeeded=True, output=output, schema_ref="s@v1", payload_fields={})


def _director(
    executor: ScriptedExecutor,
    evaluator: SequenceEvaluator | None,
    store: RunStore | None = None,
) -> ContentDirector:
    binding = SchemaBinding("s@v1", _schema())
    return ContentDirector(
        executor,
        {"researcher@v1": binding, "writer@v1": binding},
        qa=evaluator,
        store=store,
    )


def _new_run(*, max_reworks: int = 3) -> Run:
    return Run.create(
        run_id="run-rwr-0001",
        content_brief_ref="brief-rwr",
        workflow_version_ref="wf@v1",
        rework_policy=ReworkPolicy(max_rework_iterations=max_reworks),
    )


def _escalate(
    executor: ScriptedExecutor,
    evaluator: SequenceEvaluator,
    *,
    run: Run | None = None,
    store: RunStore | None = None,
) -> tuple[ContentDirector, Run]:
    """Run v1 to a QA escalation with one pending Human Review."""
    active = _new_run() if run is None else run
    director = _director(executor, evaluator, store)
    director.execute(active, REQUESTS)
    assert active.status is RunStatus.WAITING_HUMAN
    assert active.human_reviews[-1].status is ReviewStatus.PENDING
    return director, active


def _request_changes(run: Run, instructions: str = "Add a primary source.") -> None:
    run.submit_review(
        run.human_reviews[-1].review_id,
        ReviewStatus.CHANGES_REQUESTED,
        by=REVIEWER,
        reason=instructions,
    )


def test_rwr_01_03_changes_reexecute_only_the_producer_and_start_fresh_gates() -> None:
    """RWR-01/03: v1 -> producer Task -> v2, then only v2 receives fresh QA/review."""
    executor = ScriptedExecutor([_success("script v2")])
    evaluator = SequenceEvaluator([FLAGGED, PASSED])
    director, run = _escalate(executor, evaluator)
    v1 = run.artifacts[-1]
    _request_changes(run)

    director.resume(run, REQUESTS)

    assert run.status is RunStatus.WAITING_HUMAN
    assert run.rework_count == 1
    assert [task.workflow_step_ref for task in run.tasks] == ["research", "write", "write"]
    assert [task.agent_ref for task in run.tasks] == [
        "researcher@v1",
        "writer@v1",
        "writer@v1",
    ]
    research, old, current = run.artifacts
    assert research.status is ArtifactStatus.DRAFT
    assert old.artifact_id == v1.artifact_id
    assert old.status is ArtifactStatus.SUPERSEDED
    assert (current.version, current.supersedes_ref, current.content) == (
        2,
        old.artifact_id,
        "script v2",
    )
    assert current.status is ArtifactStatus.CANDIDATE
    assert [evaluation.artifact_ref for evaluation in run.evaluations] == [
        old.artifact_id,
        current.artifact_id,
    ]
    assert [review.artifact_ref for review in run.human_reviews] == [
        old.artifact_id,
        current.artifact_id,
    ]
    assert run.human_reviews[-1].status is ReviewStatus.PENDING
    assert evaluator.calls == ["script v1", "script v2"]


def test_rwr_02_rework_task_persists_canonical_feedback_json() -> None:
    """RWR-02: the rework call gets exact content, QA flags and human instructions."""
    executor = ScriptedExecutor([_success("script v2")])
    evaluator = SequenceEvaluator([FLAGGED, PASSED])
    director, run = _escalate(executor, evaluator)
    v1 = run.artifacts[-1]
    _request_changes(run, "Cite the randomized trial.")

    director.resume(run, REQUESTS)

    stored = run.tasks[-1].task_input
    assert stored == json.dumps(
        {
            "artifact": {"content": "script v1", "ref": v1.artifact_id, "version": 1},
            "human_instructions": "Cite the randomized trial.",
            "qa_flags": ["risk in script v1"],
            "rework_iteration": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert executor.calls[-1] == stored


def test_rwr_04_repeated_changes_form_a_linear_three_version_chain() -> None:
    """RWR-04: a second reviewed risk appends one Task and creates v3, never a fork."""
    executor = ScriptedExecutor([_success("script v2"), _success("script v3")])
    evaluator = SequenceEvaluator([FLAGGED, FLAGGED, PASSED])
    director, run = _escalate(executor, evaluator)
    _request_changes(run, "Fix v1.")
    director.resume(run, REQUESTS)
    _request_changes(run, "Fix v2.")

    director.resume(run, REQUESTS)

    scripts = run.artifacts[1:]
    assert run.rework_count == 2
    assert len(run.tasks) == len(REQUESTS) + 2
    assert [artifact.version for artifact in scripts] == [1, 2, 3]
    assert [artifact.supersedes_ref for artifact in scripts] == [
        None,
        scripts[0].artifact_id,
        scripts[1].artifact_id,
    ]
    assert [artifact.status for artifact in scripts] == [
        ArtifactStatus.SUPERSEDED,
        ArtifactStatus.SUPERSEDED,
        ArtifactStatus.CANDIDATE,
    ]


@pytest.mark.parametrize(
    "decision", [ReviewStatus.PENDING, ReviewStatus.APPROVED, ReviewStatus.REJECTED]
)
def test_rwr_05_only_changes_requested_starts_rework(decision: ReviewStatus) -> None:
    """RWR-05: pending/approved/rejected escalation Reviews cause no producer call."""
    executor = ScriptedExecutor([_success("unused")])
    evaluator = SequenceEvaluator([FLAGGED])
    director, run = _escalate(executor, evaluator)
    if decision is not ReviewStatus.PENDING:
        run.submit_review(run.human_reviews[-1].review_id, decision, by=REVIEWER, reason="decision")
    before = run.snapshot
    executor.calls.clear()

    director.resume(run, REQUESTS)

    assert executor.calls == []
    assert run.snapshot == before


def test_rwr_06_rework_without_qa_stops_at_the_fresh_gate() -> None:
    """RWR-06: a missing evaluator cannot turn a reworked DRAFT into accepted content."""
    initial = ScriptedExecutor([])
    _, run = _escalate(initial, SequenceEvaluator([FLAGGED]))
    _request_changes(run)
    reworker = ScriptedExecutor([_success("script v2")])

    _director(reworker, None).resume(run, REQUESTS)

    assert run.status is RunStatus.WAITING_QA
    assert run.artifacts[-1].status is ArtifactStatus.DRAFT
    assert len(run.evaluations) == 1
    assert len(run.human_reviews) == 1


def test_rwf_01_failed_rework_fails_run_without_superseding_candidate() -> None:
    """RWF-01: a failed correction Task preserves the reviewed v1 and creates no v2."""
    executor = ScriptedExecutor([ExecutionResult(succeeded=False, failure_reason="cannot revise")])
    evaluator = SequenceEvaluator([FLAGGED])
    director, run = _escalate(executor, evaluator)
    v1 = run.artifacts[-1].artifact_id
    _request_changes(run)

    director.resume(run, REQUESTS)

    assert run.status is RunStatus.FAILED
    assert run.failure_reason == "one or more tasks failed"
    assert len(run.artifacts) == 2
    assert run.artifact(v1).status is ArtifactStatus.CANDIDATE


def test_rwf_02_outputless_rework_fails_without_superseding_candidate() -> None:
    """RWF-02: SUCCEEDED without validated Output is explicit failure, not a fake version."""
    executor = ScriptedExecutor([ExecutionResult(succeeded=True)])
    evaluator = SequenceEvaluator([FLAGGED])
    director, run = _escalate(executor, evaluator)
    v1 = run.artifacts[-1].artifact_id
    _request_changes(run)

    director.resume(run, REQUESTS)

    assert run.status is RunStatus.FAILED
    assert run.failure_reason == REWORK_NO_OUTPUT_REASON
    assert len(run.artifacts) == 2
    assert run.artifact(v1).status is ArtifactStatus.CANDIDATE


def test_rwf_03_exhausted_rework_policy_fails_before_opening_or_calling() -> None:
    """RWF-03: the Director converts the refused iteration to an observable terminal failure."""
    executor = ScriptedExecutor([_success("unused")])
    evaluator = SequenceEvaluator([FLAGGED])
    run = _new_run(max_reworks=0)
    director, run = _escalate(executor, evaluator, run=run)
    _request_changes(run)
    executor.calls.clear()

    director.resume(run, REQUESTS)

    assert run.status is RunStatus.FAILED
    assert run.failure_reason == REWORK_LIMIT_REASON
    assert run.rework_count == 0
    assert len(run.tasks) == len(REQUESTS)
    assert len(run.artifacts) == 2
    assert executor.calls == []


def test_rwf_04_plan_mismatch_is_refused_before_rework_mutates_anything() -> None:
    """RWF-04: a changed final producer cannot consume the stored candidate by guesswork."""
    executor = ScriptedExecutor([_success("unused")])
    evaluator = SequenceEvaluator([FLAGGED])
    director, run = _escalate(executor, evaluator)
    _request_changes(run)
    before = run.snapshot
    wrong = [*REQUESTS[:-1], TaskRequest("rewrite", "other@v1", "")]

    with pytest.raises(RunResumptionError, match="but the plan has step"):
        director.resume(run, wrong)

    assert run.snapshot == before


@pytest.mark.parametrize(
    ("crash_after", "calls_before_crash", "calls_after_restart", "attempt_count"),
    [(1, 0, 1, 1), (2, 0, 1, 2), (3, 1, 0, 1)],
)
def test_rws_01_03_every_rework_boundary_resumes_without_duplicate_committed_work(
    crash_after: int,
    calls_before_crash: int,
    calls_after_restart: int,
    attempt_count: int,
) -> None:
    """RWS-01..03: RUNNING, Task-start and version commits each recover from Run truth."""
    store = SnapshotStore()
    initial = ScriptedExecutor([])
    _, run = _escalate(initial, SequenceEvaluator([FLAGGED]), store=store)
    _request_changes(run)
    first = ScriptedExecutor([_success("script v2")])

    with pytest.raises(ProcessCrashError):
        _director(first, SequenceEvaluator([PASSED]), CrashAfterSave(store, crash_after)).resume(
            run, REQUESTS
        )

    restored = store.load(run.run_id)
    assert restored is not None
    second = ScriptedExecutor([_success("script v2")])
    _director(second, SequenceEvaluator([PASSED]), store).resume(restored, REQUESTS)

    assert len(first.calls) == calls_before_crash
    assert len(second.calls) == calls_after_restart
    assert restored.status is RunStatus.WAITING_HUMAN
    assert restored.rework_count == 1
    assert len(restored.tasks) == len(REQUESTS) + 1
    assert restored.tasks[-1].attempt_count == attempt_count
    assert [artifact.version for artifact in restored.artifacts[1:]] == [1, 2]


def test_rws_04_terminal_reworked_run_is_a_no_op_on_resume() -> None:
    """RWS-04: once v2 is approved/published and Run completed, resume calls nothing."""
    executor = ScriptedExecutor([_success("script v2")])
    evaluator = SequenceEvaluator([FLAGGED, PASSED])
    director, run = _escalate(executor, evaluator)
    _request_changes(run)
    director.resume(run, REQUESTS)
    current = run.artifacts[-1]
    run.submit_review(run.human_reviews[-1].review_id, ReviewStatus.APPROVED, by=REVIEWER)
    run.transition_artifact(current.artifact_id, ArtifactStatus.APPROVED, by=CD)
    run.transition_artifact(current.artifact_id, ArtifactStatus.PUBLISHED, by=CD)
    run.transition(RunStatus.COMPLETED, by=CD)
    before = run.snapshot
    executor.calls.clear()

    director.resume(run, REQUESTS)

    assert executor.calls == []
    assert run.snapshot == before
