"""QA call metrics (ADR-0036): the ``LLMArtifactEvaluator`` and Evaluation-attributed records.

AEV (``ANALYTICS_RECORD_ACCEPTANCE.md`` §6): Run records an evaluator's call against the Evaluation
and derives the role from its ``evaluator_ref``, never from the caller. EFL-07 / LAE
(``EVALUATION_ACCEPTANCE.md`` §2, §4.3): the LLM-backed QA evaluator decides only through
``decode_verdict`` and reports every completed call — on a verdict, a malformed answer and a model
failure alike — so the gate stays fail closed and no call goes unrecorded. Deterministic fakes of
the ``LLMClient`` port only: no SDK, network or system clock.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from omemo_content_factory.application.content_director import ContentDirector, TaskRequest
from omemo_content_factory.application.qa_evaluation import (
    QaCallError,
    QaVerdictError,
    evaluate_artifact,
)
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.domain.analytics import (
    AnalyticsRecord,
    AnalyticsRecordCaptured,
    Cost,
    InvalidAnalyticsRecordError,
    TimeRange,
    TokenUsage,
)
from omemo_content_factory.domain.artifact import ArtifactQaNotPassedError, ArtifactStatus
from omemo_content_factory.domain.evaluation import (
    EvaluationCompleted,
    EvaluationStatus,
    InvalidEvaluationError,
)
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import (
    Actor,
    Run,
    RunRestorationError,
    RunSnapshot,
    RunStatus,
    UnauthorizedActorError,
)
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.task import TaskStatus
from omemo_content_factory.infrastructure.llm import (
    LLMArtifactEvaluator,
    LLMCallMetrics,
    LLMCompletion,
    LLMError,
)
from omemo_content_factory.infrastructure.run_snapshot_codec import (
    FORMAT_VERSION,
    SnapshotFormatError,
    decode_snapshot,
    encode_snapshot,
)
from omemo_content_factory.tools.toolbox import Toolbox

CD = Actor.CONTENT_DIRECTOR
REVIEWER = Actor.HUMAN_REVIEWER
QA_REF = "qa_agent@v1"
PROMPT_REF = "qa-agent@v1"
T0 = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
USAGE = TokenUsage(input_tokens=300, output_tokens=40)
COST = Cost(amount=Decimal("0.0015"), currency="USD")
SPAN = TimeRange(started_at=T0, finished_at=T0 + timedelta(seconds=2))
SYSTEM = "Ты — QA. Верни verdict и flags."
TEMPLATE = "Материал на проверку:\n{input}\n\nВерни: verdict, flags."
REQUESTS = [TaskRequest("step-write", "writer@v1", "бриф", artifact_kind="script")]


def _turn(model: str = "qa-model") -> LLMCallMetrics:
    """One completed provider turn, measured exactly as ``USAGE`` / ``COST`` / ``SPAN``."""
    return LLMCallMetrics(
        provider="test",
        model=model,
        input_tokens=USAGE.input_tokens,
        output_tokens=USAGE.output_tokens,
        cost_amount=COST.amount,
        cost_currency=COST.currency,
        started_at=SPAN.started_at,
        finished_at=SPAN.finished_at,
    )


@dataclass
class ScriptedQaClient:
    """Fake ``LLMClient``: answers ``fields`` in one measured turn, or raises ``error``."""

    fields: dict[str, str] = field(default_factory=lambda: {"verdict": "passed", "flags": "[]"})
    error: LLMError | None = None
    calls: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox
    ) -> LLMCompletion:
        self.calls.append((system, user, tuple(fields)))
        if self.error is not None:
            raise self.error
        return LLMCompletion(self.fields, (_turn(),))


def qa_evaluator(client: ScriptedQaClient) -> LLMArtifactEvaluator:
    return LLMArtifactEvaluator(
        client=client,
        system_prompt=SYSTEM,
        user_template=TEMPLATE,
        output_fields=("verdict", "flags"),
        prompt_ref=PROMPT_REF,
        evaluator_ref=QA_REF,
    )


def make_run() -> Run:
    return Run.create(
        run_id="run-qam-0001", content_brief_ref="brief-0001", workflow_version_ref="wf@v1"
    )


def candidate(run: Run, content: str = "Сон важен для восстановления.") -> str:
    """Produce a CANDIDATE Artifact carrying ``content`` directly through the Run root."""
    task_id = run.open_task(workflow_step_ref="s", agent_ref="w@v1", task_input="i", by=CD)
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=CD)
    output_id = run.record_output(task_id, payload=content, schema_ref="s@v1", by=CD)
    artifact_id = run.create_artifact(output_id, kind="script", by=CD)
    run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, by=CD)
    return artifact_id


def record_call(run: Run, evaluation_id: str, *, by: Actor = CD) -> str:
    return run.record_evaluation_analytics(
        evaluation_id,
        provider="anthropic",
        model="qa-model",
        token_usage=USAGE,
        cost=COST,
        time_range=SPAN,
        by=by,
        prompt_ref=PROMPT_REF,
    )


def run_with_qa_call() -> Run:
    run = make_run()
    evaluation_id = run.open_evaluation(candidate(run), kind="qa", by=CD, evaluator_ref=QA_REF)
    record_call(run, evaluation_id)
    return run


def assert_gate_shut(run: Run, artifact_id: str) -> None:
    """The Evaluation stayed ``PENDING`` and even a human Approve cannot approve the candidate."""
    assert [view.status for view in run.evaluations] == [EvaluationStatus.PENDING]
    review_id = run.open_human_review(artifact_id, by=CD)
    run.submit_review(review_id, ReviewStatus.APPROVED, by=REVIEWER)
    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)


# --- Evaluation-attributed records (AEV) -------------------------------------------------


def test_aev_01_an_evaluator_call_is_attributed_to_its_evaluation_and_role() -> None:
    run = make_run()
    evaluation_id = run.open_evaluation(candidate(run), kind="qa", by=CD, evaluator_ref=QA_REF)

    record_id = record_call(run, evaluation_id)

    record = run.analytics_record(record_id)
    assert run.evaluation(evaluation_id).evaluator_ref == QA_REF
    assert (record.run_id, record.task_id, record.evaluation_id, record.agent_ref) == (
        run.run_id,
        None,
        evaluation_id,
        QA_REF,
    )
    assert (record.retries, record.prompt_ref) == (None, PROMPT_REF)
    assert (record.token_usage, record.cost, record.time_range) == (USAGE, COST, SPAN)
    assert run.events[-1] == AnalyticsRecordCaptured(
        run_id=run.run_id,
        record_id=record_id,
        task_id=None,
        agent_ref=QA_REF,
        evaluation_id=evaluation_id,
    )


def test_aev_02_an_evaluation_that_names_no_evaluator_records_nothing() -> None:
    run = make_run()
    evaluation_id = run.open_evaluation(candidate(run), kind="qa", by=CD)
    journal = len(run.events)

    with pytest.raises(InvalidAnalyticsRecordError, match="names no evaluator"):
        record_call(run, evaluation_id)

    assert run.analytics_records == ()
    assert len(run.events) == journal
    task_id = run.tasks[0].task_id
    next_id = run.record_analytics(
        task_id, provider="p", model="m", token_usage=USAGE, cost=COST, time_range=SPAN, by=CD
    )
    assert next_id == f"{run.run_id}-analytics-1"


def test_aev_03_an_unknown_evaluation_or_another_actor_is_refused() -> None:
    run = make_run()
    evaluation_id = run.open_evaluation(candidate(run), kind="qa", by=CD, evaluator_ref=QA_REF)

    with pytest.raises(KeyError):
        record_call(run, f"{run.run_id}-evaluation-9")
    for actor in (actor for actor in Actor if actor is not CD):
        with pytest.raises(UnauthorizedActorError):
            record_call(run, evaluation_id, by=actor)
    assert run.analytics_records == ()


def test_aev_04_a_call_on_a_decided_evaluation_is_still_a_fact() -> None:
    run = make_run()
    evaluation_id = run.open_evaluation(candidate(run), kind="qa", by=CD, evaluator_ref=QA_REF)
    run.record_evaluation(evaluation_id, EvaluationStatus.PASSED, by=CD)

    record_call(run, evaluation_id)

    assert [record.evaluation_id for record in run.analytics_records] == [evaluation_id]


_TASK_RECORD = AnalyticsRecord(
    record_id="r-analytics-1",
    run_id="r",
    task_id="r-task-1",
    agent_ref="a@v1",
    provider="p",
    model="m",
    token_usage=USAGE,
    cost=COST,
    time_range=SPAN,
    retries=0,
)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"evaluation_id": "r-evaluation-1"}, id="two-subjects"),
        pytest.param({"task_id": None}, id="no-subject"),
        pytest.param({"retries": None}, id="task-without-retries"),
        pytest.param({"task_id": None, "evaluation_id": "r-evaluation-1"}, id="evaluation-retries"),
    ],
)
def test_aev_05_a_record_names_exactly_one_subject_with_its_retry_rule(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(InvalidAnalyticsRecordError):
        dataclasses.replace(_TASK_RECORD, **changes)


def test_aev_05_an_evaluation_record_carries_no_retry_count() -> None:
    record = dataclasses.replace(
        _TASK_RECORD, task_id=None, evaluation_id="r-evaluation-1", retries=None
    )
    assert (record.task_id, record.evaluation_id, record.retries) == (None, "r-evaluation-1", None)


def test_aev_06_an_evaluation_record_survives_restore_and_the_format_2_codec() -> None:
    run = run_with_qa_call()

    assert Run.restore(run.snapshot).snapshot == run.snapshot
    document = encode_snapshot(run.snapshot)
    assert document["format"] == FORMAT_VERSION == 2
    assert decode_snapshot(document) == run.snapshot
    with pytest.raises(SnapshotFormatError):
        decode_snapshot({**document, "format": 1})


def _with_record(snapshot: RunSnapshot, **changes: Any) -> RunSnapshot:
    record = dataclasses.replace(snapshot.analytics_records[0], **changes)
    return dataclasses.replace(snapshot, analytics_records=(record,))


@pytest.mark.parametrize("changes", [{"evaluation_id": "nowhere"}, {"agent_ref": "someone@v1"}])
def test_aev_06_a_record_off_its_evaluation_or_evaluator_is_refused(
    changes: dict[str, str],
) -> None:
    snapshot = run_with_qa_call().snapshot

    with pytest.raises(RunRestorationError, match="owned Evaluation and its evaluator"):
        Run.restore(_with_record(snapshot, **changes))


def test_aev_06_a_record_on_an_evaluation_without_evaluator_is_refused() -> None:
    snapshot = run_with_qa_call().snapshot
    evaluation = dataclasses.replace(snapshot.evaluations[0], evaluator_ref=None)

    with pytest.raises(RunRestorationError, match="owned Evaluation and its evaluator"):
        Run.restore(dataclasses.replace(snapshot, evaluations=(evaluation,)))


@pytest.mark.parametrize("blank", ["", "   "])
def test_efl_07_a_blank_evaluator_is_refused_and_opens_nothing(blank: str) -> None:
    run = make_run()
    artifact_id = candidate(run)

    with pytest.raises(InvalidEvaluationError):
        run.open_evaluation(artifact_id, kind="qa", by=CD, evaluator_ref=blank)

    assert run.evaluations == ()


# --- The LLM-backed QA evaluator (LAE) ---------------------------------------------------


def test_lae_01_the_evaluator_renders_the_prompt_and_decodes_through_the_contract() -> None:
    client = ScriptedQaClient({"verdict": " FLAGGED ", "flags": '["нет источника"]'})

    result = qa_evaluator(client).evaluate("Магний лечит всё.")

    assert client.calls == [
        (
            SYSTEM,
            "Материал на проверку:\nМагний лечит всё.\n\nВерни: verdict, flags.",
            ("verdict", "flags"),
        )
    ]
    assert (result.verdict, result.flags) == (EvaluationStatus.FLAGGED, ("нет источника",))
    [measurement] = result.analytics
    assert (measurement.provider, measurement.model, measurement.prompt_ref) == (
        "test",
        "qa-model",
        PROMPT_REF,
    )
    assert (measurement.token_usage, measurement.cost, measurement.time_range) == (
        USAGE,
        COST,
        SPAN,
    )


def test_lae_02_evaluate_artifact_records_the_call_on_the_evaluation_before_the_verdict() -> None:
    run = make_run()

    evaluation_id = evaluate_artifact(run, qa_evaluator(ScriptedQaClient()), candidate(run))

    view = run.evaluation(evaluation_id)
    assert (view.status, view.evaluator_ref) == (EvaluationStatus.PASSED, QA_REF)
    [record] = run.analytics_records
    assert (record.evaluation_id, record.agent_ref, record.task_id, record.retries) == (
        evaluation_id,
        QA_REF,
        None,
        None,
    )
    assert [type(event) for event in run.events[-2:]] == [
        AnalyticsRecordCaptured,
        EvaluationCompleted,
    ]


def test_lae_03_a_malformed_answer_fails_closed_and_its_call_is_recorded() -> None:
    run = make_run()
    artifact_id = candidate(run)
    client = ScriptedQaClient({"verdict": "pass", "flags": "[]"})

    with pytest.raises(QaVerdictError) as caught:
        evaluate_artifact(run, qa_evaluator(client), artifact_id)

    assert len(caught.value.analytics) == 1
    [record] = run.analytics_records
    assert (record.evaluation_id, record.agent_ref) == (run.evaluations[0].evaluation_id, QA_REF)
    assert_gate_shut(run, artifact_id)


def test_lae_04_a_model_failure_fails_closed_keeping_its_completed_turns() -> None:
    run = make_run()
    artifact_id = candidate(run)
    failure = LLMError("budget exhausted", metrics=(_turn(), _turn("qa-model-2")))

    with pytest.raises(QaCallError) as caught:
        evaluate_artifact(run, qa_evaluator(ScriptedQaClient(error=failure)), artifact_id)

    assert caught.value.__cause__ is failure
    assert [record.model for record in run.analytics_records] == ["qa-model", "qa-model-2"]
    assert_gate_shut(run, artifact_id)


def test_lae_04_a_failure_without_a_provider_response_records_nothing() -> None:
    run = make_run()
    artifact_id = candidate(run)
    client = ScriptedQaClient(error=LLMError("connection refused"))

    with pytest.raises(QaCallError):
        evaluate_artifact(run, qa_evaluator(client), artifact_id)

    assert run.analytics_records == ()
    assert_gate_shut(run, artifact_id)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"output_fields": ("verdict",)}, id="no-flags-field"),
        pytest.param({"output_fields": ()}, id="empty-shape"),
        pytest.param({"prompt_ref": " "}, id="blank-prompt-ref"),
        pytest.param({"evaluator_ref": ""}, id="blank-evaluator-ref"),
    ],
)
def test_lae_05_construction_requires_the_verdict_shape_and_both_references(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(qa_evaluator(ScriptedQaClient()), **changes)


# --- Through the Content Director (LAE-06/07) --------------------------------------------


@dataclass(frozen=True, slots=True)
class WritingExecutor:
    """Deterministic producer: every Task succeeds with a structured, schema-bound Output."""

    def execute(self, task_input: str) -> ExecutionResult:
        return ExecutionResult(
            succeeded=True, output=f"Сценарий: {task_input}", schema_ref="s@v1", payload_fields={}
        )


class SnapshotStore:
    """Whole-Run in-memory store: keeps the last committed snapshot of each Run."""

    def __init__(self) -> None:
        self.saved: dict[str, RunSnapshot] = {}

    def save(self, run: Run) -> None:
        self.saved[run.run_id] = run.snapshot

    def load(self, run_id: str) -> Run | None:
        snapshot = self.saved.get(run_id)
        return None if snapshot is None else Run.restore(snapshot)


def director(client: ScriptedQaClient, store: SnapshotStore | None = None) -> ContentDirector:
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=[]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return ContentDirector(
        WritingExecutor(),
        {"writer@v1": SchemaBinding("s@v1", schema)},
        qa=qa_evaluator(client),
        store=store,
    )


def test_lae_06_the_director_commits_a_failed_qa_call_before_the_error_propagates() -> None:
    store = SnapshotStore()
    run = make_run()

    with pytest.raises(QaVerdictError):
        director(ScriptedQaClient({"verdict": "", "flags": "[]"}), store).execute(run, REQUESTS)

    stored = store.load(run.run_id)
    assert stored is not None
    assert stored.status is RunStatus.WAITING_QA
    [evaluation] = stored.evaluations
    assert (evaluation.status, evaluation.evaluator_ref) == (EvaluationStatus.PENDING, QA_REF)
    assert [(r.evaluation_id, r.agent_ref) for r in stored.analytics_records] == [
        (evaluation.evaluation_id, QA_REF)
    ]


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"verdict": "passed", "flags": "[]"}, RunStatus.COMPLETED),
        ({"verdict": "flagged", "flags": '["нет источника"]'}, RunStatus.WAITING_HUMAN),
    ],
)
def test_lae_07_the_director_routes_a_model_verdict_and_records_the_qa_call(
    fields: dict[str, str], status: RunStatus
) -> None:
    client = ScriptedQaClient(fields)
    run = make_run()

    director(client).execute(run, REQUESTS)

    assert run.status is status
    assert [user for _, user, _ in client.calls] == [
        "Материал на проверку:\nСценарий: бриф\n\nВерни: verdict, flags."
    ]
    [evaluation] = run.evaluations
    assert [(r.evaluation_id, r.agent_ref, r.prompt_ref) for r in run.analytics_records] == [
        (evaluation.evaluation_id, QA_REF, PROMPT_REF)
    ]
