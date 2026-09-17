"""Tests for the storage wiring of the Content Director (ADR-0026).

Maps ADAPTER_ACCEPTANCE.md §7 (SWR). The Director commits every orchestration step to its store and
resumes a restored Run from the Run's own state. A "crash" is an exception thrown by an executor, an
evaluator or the store right after a write; nothing after it happens in that "process". A "restart"
is a new ``SqliteRunStore`` on the same file (or ``Run.restore`` of the last saved snapshot) and a
new Director. Executors and the evaluator are deterministic and count their calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.content_director import (
    RESUME_LIMIT_REASON,
    ContentDirector,
    RunResumptionError,
    TaskRequest,
)
from omemo_content_factory.application.qa_evaluation import EvaluationResult
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.composition import (
    DEFAULT_RUN_STORE_PATH,
    RUN_STORE_PATH_VAR,
    build_run_store,
    compile_runtime,
    run_store_path,
)
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import (
    Actor,
    InvalidTransitionError,
    Run,
    RunSnapshot,
    RunStatus,
)
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.task import TaskRetryPolicy, TaskStatus
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.sqlite_run_store import SqliteRunStore

CD = Actor.CONTENT_DIRECTOR
RUN_ID = "run-swr-0001"
REQUESTS = [
    TaskRequest("research", "researcher@v1", "brief", artifact_kind="research"),
    TaskRequest("write", "writer@v1", "", artifact_kind="script"),
]
SECOND_INPUT = "[gen] brief"  # the first step's Output, chained into the second step
FINAL_CONTENT = "[gen] [gen] brief"
WORKFLOW = Workflow.create(
    workflow_id="swr@v1",
    name="Research -> Write",
    steps=[
        WorkflowStep(step_id="research", task_type="t", agent_ref="researcher@v1", schema_ref="s"),
        WorkflowStep(step_id="write", task_type="t", agent_ref="writer@v1", schema_ref="s"),
    ],
)

PASSED = EvaluationStatus.PASSED
FLAGGED = EvaluationStatus.FLAGGED
R, S = TaskStatus.RUNNING, TaskStatus.SUCCEEDED


class _CrashError(Exception):
    """The process dies here."""


# --- Doubles ------------------------------------------------------------------------------


@dataclass
class CountingExecutor:
    """Succeeds with a structured Output and records every input it is called with.

    ``crash_on`` kills the process during the call for that input; ``fail_on`` makes that input's
    Task fail; ``observe`` looks at the world at the moment of the call.
    """

    crash_on: str | None = None
    fail_on: str | None = None
    observe: Callable[[str], None] | None = None
    calls: list[str] = field(default_factory=list)

    def execute(self, task_input: str) -> ExecutionResult:
        self.calls.append(task_input)
        if self.observe is not None:
            self.observe(task_input)
        if task_input == self.crash_on:
            raise _CrashError
        if task_input == self.fail_on:
            return ExecutionResult(succeeded=False, failure_reason="cannot")
        return ExecutionResult(
            succeeded=True, output=f"[gen] {task_input}", schema_ref="s@v1", payload_fields={}
        )


@dataclass
class CountingEvaluator:
    """Returns a fixed verdict and counts its calls; ``crash`` kills the process during the call."""

    verdict: EvaluationStatus = PASSED
    crash: bool = False
    observe: Callable[[], None] | None = None
    calls: int = 0
    evaluator_ref: str = "qa@v1"

    def evaluate(self, content: str) -> EvaluationResult:
        self.calls += 1
        if self.observe is not None:
            self.observe()
        if self.crash:
            raise _CrashError
        return EvaluationResult(verdict=self.verdict)


class RecordingStore:
    """A ``RunStore`` keeping every saved snapshot in order, in memory."""

    def __init__(self) -> None:
        self.snapshots: list[RunSnapshot] = []

    def save(self, run: Run, /) -> None:
        self.snapshots.append(run.snapshot)

    def load(self, run_id: str, /) -> Run | None:
        return Run.restore(self.snapshots[-1]) if self.snapshots else None

    @property
    def last(self) -> RunSnapshot:
        return self.snapshots[-1]


class CrashingStore:
    """Delegates to ``inner`` and kills the process right after its ``crash_after``-th save."""

    def __init__(self, inner: RunStore, crash_after: int) -> None:
        self._inner = inner
        self._crash_after = crash_after
        self._saves = 0

    def save(self, run: Run, /) -> None:
        self._inner.save(run)
        self._saves += 1
        if self._saves == self._crash_after:
            raise _CrashError

    def load(self, run_id: str, /) -> Run | None:
        return self._inner.load(run_id)


# --- Helpers ------------------------------------------------------------------------------


def _schema() -> Schema:
    """An ACTIVE Schema with no required fields: any structured result validates VALID."""
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=[]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


def _director(
    executor: CountingExecutor,
    store: RunStore | None,
    qa: CountingEvaluator | None = None,
) -> ContentDirector:
    schemas = {
        "researcher@v1": SchemaBinding("s@v1", _schema()),
        "writer@v1": SchemaBinding("s@v1", _schema()),
    }
    return ContentDirector(executor, schemas, qa=qa, store=store)


def _new_run() -> Run:
    return Run.create(run_id=RUN_ID, content_brief_ref="brief-swr", workflow_version_ref="swr@v1")


def _shape(snapshot: RunSnapshot) -> tuple[object, ...]:
    """What a checkpoint shows: statuses of the Run, its Tasks and QA, and child counts."""
    return (
        snapshot.status,
        tuple(task.status for task in snapshot.tasks),
        len(snapshot.artifacts),
        tuple(evaluation.status for evaluation in snapshot.evaluations),
        len(snapshot.human_reviews),
    )


def _crash_then_restart(
    path: Path, *, executor: CountingExecutor, qa: CountingEvaluator | None = None
) -> Run:
    """Run on a SQLite store until the process crashes, then load the Run in a new "process"."""
    with pytest.raises(_CrashError):
        _director(executor, SqliteRunStore(path), qa).execute(_new_run(), REQUESTS)
    restored = SqliteRunStore(path).load(RUN_ID)
    assert restored is not None
    return restored


_NO_QA_CHECKPOINTS = [
    (RunStatus.QUEUED, (), 0, (), 0),
    (RunStatus.RUNNING, (), 0, (), 0),
    (RunStatus.RUNNING, (R,), 0, (), 0),
    (RunStatus.RUNNING, (S,), 1, (), 0),
    (RunStatus.RUNNING, (S, R), 1, (), 0),
    (RunStatus.RUNNING, (S, S), 2, (), 0),
    (RunStatus.WAITING_QA, (S, S), 2, (), 0),
]
_QA_PASSED_CHECKPOINTS = [
    *_NO_QA_CHECKPOINTS,
    (RunStatus.WAITING_QA, (S, S), 2, (EvaluationStatus.PENDING,), 0),
    (RunStatus.WAITING_QA, (S, S), 2, (PASSED,), 0),
    (RunStatus.WAITING_HUMAN, (S, S), 2, (PASSED,), 1),
]


def _approve(run: Run) -> None:
    """Play the human reviewer: approve the Run's pending Review (the Approval Gate, ADR-0044)."""
    (pending,) = [view for view in run.human_reviews if view.status is ReviewStatus.PENDING]
    run.submit_review(pending.review_id, ReviewStatus.APPROVED, by=Actor.HUMAN_REVIEWER)


# --- SWR-01..03: commit points ------------------------------------------------------------


def test_swr_01_every_step_is_committed_once_in_order_without_qa() -> None:
    store = RecordingStore()
    run = _new_run()

    _director(CountingExecutor(), store).execute(run, REQUESTS)

    assert [_shape(s) for s in store.snapshots] == [
        *_NO_QA_CHECKPOINTS,
        (RunStatus.WAITING_HUMAN, (S, S), 2, (), 0),
        (RunStatus.COMPLETED, (S, S), 2, (), 0),
    ]
    assert store.last == run.snapshot


def test_swr_01_every_step_is_committed_once_in_order_through_the_qa_gate() -> None:
    passed, flagged = RecordingStore(), RecordingStore()
    passed_run, flagged_run = _new_run(), _new_run()

    _director(CountingExecutor(), passed, CountingEvaluator()).execute(passed_run, REQUESTS)
    _director(CountingExecutor(), flagged, CountingEvaluator(FLAGGED)).execute(
        flagged_run, REQUESTS
    )

    assert [_shape(s) for s in passed.snapshots] == _QA_PASSED_CHECKPOINTS
    assert passed.last == passed_run.snapshot
    _approve(passed_run)
    _director(CountingExecutor(), passed, CountingEvaluator()).resume(passed_run, REQUESTS)
    assert _shape(passed.last) == (RunStatus.COMPLETED, (S, S), 2, (PASSED,), 1)
    assert passed_run.artifacts[-1].status is ArtifactStatus.APPROVED
    assert [_shape(s) for s in flagged.snapshots] == [
        *_QA_PASSED_CHECKPOINTS[:8],
        (RunStatus.WAITING_QA, (S, S), 2, (FLAGGED,), 0),
        (RunStatus.WAITING_HUMAN, (S, S), 2, (FLAGGED,), 1),
    ]
    assert passed.last == passed_run.snapshot
    assert flagged.last == flagged_run.snapshot


def test_swr_02_each_call_is_preceded_by_its_commit_and_no_step_is_half_recorded() -> None:
    store = RecordingStore()
    seen: list[str] = []

    def before_executor(task_input: str) -> None:
        task = store.last.tasks[-1]
        assert (task.status, task.task_input) == (TaskStatus.RUNNING, task_input)
        seen.append(task_input)

    def before_evaluator() -> None:
        assert [e.status for e in store.last.evaluations] == [EvaluationStatus.PENDING]
        assert store.last.artifacts[-1].status is ArtifactStatus.CANDIDATE
        seen.append("qa")

    executor = CountingExecutor(observe=before_executor)
    _director(executor, store, CountingEvaluator(observe=before_evaluator)).execute(
        _new_run(), REQUESTS
    )

    assert seen == ["brief", SECOND_INPUT, "qa"]
    for snapshot in store.snapshots:
        made_from = {artifact.output_ref for artifact in snapshot.artifacts}
        for task in snapshot.tasks:
            if task.status is TaskStatus.SUCCEEDED:
                assert task.output is not None
                assert task.output.output_id in made_from


def test_swr_03_a_store_changes_nothing_in_the_outcome() -> None:
    with_store, without_store = _new_run(), _new_run()

    _director(CountingExecutor(), RecordingStore(), CountingEvaluator()).execute(
        with_store, REQUESTS
    )
    _director(CountingExecutor(), None, CountingEvaluator()).execute(without_store, REQUESTS)

    assert with_store.snapshot == without_store.snapshot


# --- SWR-04..06: crash and resume ---------------------------------------------------------


def test_swr_04_a_crash_during_a_call_repeats_only_that_call(tmp_path: Path) -> None:
    path = tmp_path / "runs.sqlite3"
    with pytest.raises(_CrashError):
        _director(CountingExecutor(crash_on=SECOND_INPUT), SqliteRunStore(path)).execute_workflow(
            _new_run(), WORKFLOW, brief="brief"
        )

    store = SqliteRunStore(path)
    run = store.load(RUN_ID)
    assert run is not None
    assert (run.status, [t.status for t in run.tasks]) == (RunStatus.RUNNING, [S, R])

    executor = CountingExecutor()
    _director(executor, store).resume_workflow(run, WORKFLOW, brief="brief")

    assert executor.calls == [SECOND_INPUT]
    assert run.status is RunStatus.COMPLETED
    assert [t.attempt_count for t in run.tasks] == [1, 2]
    assert [t.task_id for t in run.tasks] == [f"{RUN_ID}-task-1", f"{RUN_ID}-task-2"]
    assert [a.artifact_id for a in run.artifacts] == [
        f"{RUN_ID}-artifact-1",
        f"{RUN_ID}-artifact-2",
    ]
    reloaded = store.load(RUN_ID)
    assert reloaded is not None
    assert reloaded.snapshot == run.snapshot


@pytest.mark.parametrize("crash_after", range(1, len(_QA_PASSED_CHECKPOINTS) + 1))
def test_swr_05_a_crash_after_any_commit_resumes_without_repeating_work(
    tmp_path: Path, crash_after: int
) -> None:
    path = tmp_path / "runs.sqlite3"
    first_executor, first_qa = CountingExecutor(), CountingEvaluator()
    with pytest.raises(_CrashError):
        _director(
            first_executor, CrashingStore(SqliteRunStore(path), crash_after), first_qa
        ).execute(_new_run(), REQUESTS)

    store = SqliteRunStore(path)
    run = store.load(RUN_ID)
    assert run is not None
    assert _shape(run.snapshot) == _QA_PASSED_CHECKPOINTS[crash_after - 1]
    executor, qa = CountingExecutor(), CountingEvaluator()
    _director(executor, store, qa).resume(run, REQUESTS)
    assert run.snapshot.status is RunStatus.WAITING_HUMAN
    _approve(run)
    _director(executor, store, qa).resume(run, REQUESTS)

    assert run.status is RunStatus.COMPLETED
    assert len(run.human_reviews) == 1
    assert [a.content for a in run.artifacts] == [SECOND_INPUT, FINAL_CONTENT]
    assert sorted(first_executor.calls + executor.calls) == sorted(["brief", SECOND_INPUT])
    assert first_qa.calls + qa.calls == 1
    assert len(run.evaluations) == 1
    reloaded = store.load(RUN_ID)
    assert reloaded is not None
    assert reloaded.snapshot == run.snapshot


def test_swr_06_a_crash_during_qa_finishes_the_same_evaluation(tmp_path: Path) -> None:
    run = _crash_then_restart(
        tmp_path / "runs.sqlite3", executor=CountingExecutor(), qa=CountingEvaluator(crash=True)
    )
    assert (run.status, [e.status for e in run.evaluations]) == (
        RunStatus.WAITING_QA,
        [EvaluationStatus.PENDING],
    )
    pending = run.evaluations[0].evaluation_id

    executor, qa = CountingExecutor(), CountingEvaluator()
    _director(executor, SqliteRunStore(tmp_path / "runs.sqlite3"), qa).resume(run, REQUESTS)

    assert (executor.calls, qa.calls) == ([], 1)
    assert [(e.evaluation_id, e.status) for e in run.evaluations] == [(pending, PASSED)]
    assert (run.status, len(run.human_reviews)) == (RunStatus.WAITING_HUMAN, 1)


# --- SWR-07..10: what resume leaves alone or refuses --------------------------------------


@pytest.mark.parametrize(
    ("executor", "qa", "status"),
    [
        (CountingExecutor(), CountingEvaluator(FLAGGED), RunStatus.WAITING_HUMAN),
        (CountingExecutor(), CountingEvaluator(), RunStatus.WAITING_HUMAN),
        (CountingExecutor(), None, RunStatus.COMPLETED),
        (CountingExecutor(fail_on=SECOND_INPUT), None, RunStatus.FAILED),
    ],
    ids=["escalated-to-the-human", "waiting-for-approval", "completed", "failed"],
)
def test_swr_07_a_run_that_is_waiting_or_done_is_left_alone(
    executor: CountingExecutor, qa: CountingEvaluator | None, status: RunStatus
) -> None:
    first = RecordingStore()
    _director(executor, first, qa).execute(_new_run(), REQUESTS)
    run = Run.restore(first.last)
    assert run.status is status

    again, second, second_qa = CountingExecutor(), RecordingStore(), CountingEvaluator()
    _director(again, second, second_qa).resume(run, REQUESTS)

    assert (again.calls, second_qa.calls, second.snapshots) == ([], 0, [])
    assert run.snapshot == first.last


def test_swr_08_an_interrupted_task_without_attempts_left_is_failed() -> None:
    run = _new_run()
    run.transition(RunStatus.QUEUED, by=CD)
    run.transition(RunStatus.RUNNING, by=CD)
    task_id = run.open_task(
        "research", "researcher@v1", "brief", by=CD, retry_policy=TaskRetryPolicy(max_attempts=1)
    )
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)

    executor, store = CountingExecutor(), RecordingStore()
    _director(executor, store).resume(run, REQUESTS[:1])

    assert executor.calls == []
    task = run.task(task_id)
    assert (task.status, task.failure_reason) == (TaskStatus.FAILED, RESUME_LIMIT_REASON)
    assert run.status is RunStatus.FAILED
    assert store.last == run.snapshot


@pytest.mark.parametrize(
    "plan",
    [
        [TaskRequest("other", "researcher@v1", "brief"), REQUESTS[1]],
        [TaskRequest("research", "writer@v1", "brief"), REQUESTS[1]],
        [],
    ],
    ids=["another-step", "another-role", "fewer-steps"],
)
def test_swr_09_a_run_of_another_plan_is_refused_untouched(plan: list[TaskRequest]) -> None:
    run = _new_run()
    run.transition(RunStatus.QUEUED, by=CD)
    run.transition(RunStatus.RUNNING, by=CD)
    run.open_task("research", "researcher@v1", "brief", by=CD)
    before = run.snapshot

    executor, store = CountingExecutor(), RecordingStore()
    with pytest.raises(RunResumptionError):
        _director(executor, store).resume(run, plan)

    assert (run.snapshot, executor.calls, store.snapshots) == (before, [], [])


def test_swr_10_resume_of_a_fresh_run_executes_it_and_execute_still_wants_a_fresh_one() -> None:
    executed, resumed = _new_run(), _new_run()
    _director(CountingExecutor(), RecordingStore()).execute(executed, REQUESTS)
    _director(CountingExecutor(), RecordingStore()).resume(resumed, REQUESTS)
    assert resumed.snapshot == executed.snapshot

    store = RecordingStore()
    with pytest.raises(_CrashError):
        _director(CountingExecutor(crash_on=SECOND_INPUT), store).execute(_new_run(), REQUESTS)
    with pytest.raises(InvalidTransitionError):
        _director(CountingExecutor(), None).execute(Run.restore(store.last), REQUESTS)


# --- SWR-11: the Composition Root --------------------------------------------------------


def test_swr_11_the_store_defaults_under_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert run_store_path({}) == DEFAULT_RUN_STORE_PATH
    assert run_store_path({RUN_STORE_PATH_VAR: "  "}) == DEFAULT_RUN_STORE_PATH
    assert isinstance(build_run_store({}), SqliteRunStore)
    assert (tmp_path / DEFAULT_RUN_STORE_PATH).parent.is_dir()


def test_swr_11_a_configured_store_is_built_and_the_compiled_director_commits_to_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "runs.sqlite3"
    store = build_run_store({RUN_STORE_PATH_VAR: str(path)})
    assert run_store_path({RUN_STORE_PATH_VAR: str(path)}) == path
    assert path.parent.is_dir()

    workflow = Workflow.create(
        workflow_id="research-to-script@v1",
        name="Research -> Script",
        steps=[
            WorkflowStep(
                step_id="research",
                task_type="research",
                agent_ref=rin.AGENT_REF,
                schema_ref=rin.SCHEMA_REF,
            ),
            WorkflowStep(
                step_id="write_script",
                task_type="write_script",
                agent_ref=leo.AGENT_REF,
                schema_ref=leo.SCHEMA_REF,
            ),
        ],
    )
    director = compile_runtime(
        (*rin.AGENTS, *leo.AGENTS),
        None,
        FakeLLMClient(),
        workflow,
        {**rin.SCHEMAS, **leo.SCHEMAS},
        skill_invocations=rin.SKILL_INVOCATIONS,
        store=store,
    )
    run = _new_run()
    director.execute_workflow(run, workflow, brief="умная кофеварка")

    stored = SqliteRunStore(path).load(RUN_ID)
    assert run.status is RunStatus.COMPLETED
    assert stored is not None
    assert stored.snapshot == run.snapshot
