"""Integration tests for the QA evaluation slice and the ContentDirector QA gate (ADR-0018 §6-7).

``evaluate_artifact`` must invoke the evaluator on the candidate's content and persist its verdict
through the Run root, and must stay fail closed when the evaluator fails. ``ContentDirector`` must
route by the verdict: ``PASSED`` continues, a risk verdict stops at the Approval Gate with an
escalation review, no candidate fails the Run, and without QA nothing changes. ``decode_verdict``
must turn a QA answer's flat string fields into a verdict by exactly the ADR-0034 grammar and
refuse everything else. Scenario ids refer to ``EVALUATION_ACCEPTANCE.md``. Deterministic fakes
only; no LLM, network or infrastructure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from omemo_content_factory.application.content_director import (
    NO_CANDIDATE_REASON,
    ContentDirector,
    TaskRequest,
)
from omemo_content_factory.application.qa_evaluation import (
    QA_KIND,
    QA_VERDICT_FIELDS,
    ArtifactEvaluator,
    EvaluationResult,
    QaVerdictError,
    decode_verdict,
    evaluate_artifact,
)
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.domain.artifact import ArtifactQaNotPassedError, ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunCompleted, RunStatus
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.task import TaskStatus

CD = Actor.CONTENT_DIRECTOR
REVIEWER = Actor.HUMAN_REVIEWER
RISK_VERDICTS = [EvaluationStatus.FLAGGED, EvaluationStatus.FAILED]

REQUESTS = [
    TaskRequest("step-research", "researcher@v1", "brief", artifact_kind="research"),
    TaskRequest("step-write", "writer@v1", "", artifact_kind="script"),
]
FINAL_CONTENT = "[gen] [gen] brief"  # the second step's Output, chained from the first


def _open_schema() -> Schema:
    """An ACTIVE Schema with no required fields — any structured result validates VALID."""
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=[]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


@dataclass(frozen=True, slots=True)
class OutputtingExecutor:
    """Deterministic executor: every Task succeeds with a structured, schema-bound Output."""

    def execute(self, task_input: str) -> ExecutionResult:
        return ExecutionResult(
            succeeded=True, output=f"[gen] {task_input}", schema_ref="s@v1", payload_fields={}
        )


@dataclass
class RecordingEvaluator:
    """Deterministic QA double: returns a fixed result and records the content it was shown."""

    result: EvaluationResult
    seen: list[str] = field(default_factory=list)

    def evaluate(self, content: str) -> EvaluationResult:
        self.seen.append(content)
        return self.result


class FailingEvaluator:
    """QA double whose backend is unavailable — a technical failure, not a verdict."""

    def evaluate(self, content: str) -> EvaluationResult:
        raise RuntimeError("QA backend unavailable")


@dataclass(frozen=True, slots=True)
class DecodingEvaluator:
    """QA double shaped like a structured-port evaluator: fixed fields -> ``decode_verdict``."""

    fields: Mapping[str, str]

    def evaluate(self, content: str) -> EvaluationResult:
        return decode_verdict(self.fields)


def make_run() -> Run:
    return Run.create(
        run_id="run-qa-app-0001", content_brief_ref="brief-0001", workflow_version_ref="wf@v1"
    )


def candidate_artifact(run: Run, content: str) -> str:
    """Produce a CANDIDATE Artifact carrying ``content`` directly through the Run root."""
    task_id = run.open_task(workflow_step_ref="s", agent_ref="w@v1", task_input="i", by=CD)
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=CD)
    output_id = run.record_output(task_id, payload=content, schema_ref="s@v1", by=CD)
    artifact_id = run.create_artifact(output_id, kind="script", by=CD)
    run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, by=CD)
    return artifact_id


def run_director(qa: ArtifactEvaluator | None, *, with_schemas: bool = True) -> Run:
    """Create a Run and orchestrate the two-step workflow through a ContentDirector."""
    schemas = {
        "researcher@v1": SchemaBinding("s@v1", _open_schema()),
        "writer@v1": SchemaBinding("s@v1", _open_schema()),
    }
    director = ContentDirector(OutputtingExecutor(), schemas if with_schemas else None, qa=qa)
    run = make_run()
    director.execute(run, REQUESTS)
    return run


# --- Application slice (EAP) -------------------------------------------------------------


def test_eap_01_evaluate_artifact_shows_the_content_and_records_the_verdict() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run, "magnesium cures everything")
    evaluator = RecordingEvaluator(EvaluationResult(EvaluationStatus.FLAGGED, ("overclaim",)))
    evaluation_id = evaluate_artifact(run, evaluator, artifact_id)
    assert evaluator.seen == ["magnesium cures everything"]
    view = run.evaluation(evaluation_id)
    assert view.artifact_ref == artifact_id
    assert view.kind == QA_KIND
    assert view.status is EvaluationStatus.FLAGGED
    assert view.flags == ("overclaim",)


def test_eap_02_evaluator_failure_propagates_and_keeps_the_gate_shut() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run, "content")
    with pytest.raises(RuntimeError, match="QA backend unavailable"):
        evaluate_artifact(run, FailingEvaluator(), artifact_id)
    assert [view.status for view in run.evaluations] == [EvaluationStatus.PENDING]
    review_id = run.open_human_review(artifact_id, by=CD)
    run.submit_review(review_id, ReviewStatus.APPROVED, by=REVIEWER)
    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)


# --- Verdict decoder (QVD, ADR-0034) -----------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("passed", EvaluationStatus.PASSED),
        ("flagged", EvaluationStatus.FLAGGED),
        ("failed", EvaluationStatus.FAILED),
        ("  FLAGGED\n", EvaluationStatus.FLAGGED),
        ("Passed", EvaluationStatus.PASSED),
    ],
)
def test_qvd_01_each_verdict_token_decodes_ignoring_case_and_surrounding_space(
    token: str, expected: EvaluationStatus
) -> None:
    result = decode_verdict({"verdict": token, "flags": '["reason"]'})
    assert result.verdict is expected


def test_qvd_02_flags_are_a_json_array_kept_verbatim_and_in_order() -> None:
    raw = '["  unsupported claim: cures anxiety ", "missing disclaimer", "missing disclaimer"]'
    result = decode_verdict({"verdict": "flagged", "flags": raw})
    assert result == EvaluationResult(
        EvaluationStatus.FLAGGED,
        ("  unsupported claim: cures anxiety ", "missing disclaimer", "missing disclaimer"),
    )
    assert decode_verdict({"verdict": "passed", "flags": "[]"}).flags == ()


def test_qvd_03_a_pass_may_carry_remarks() -> None:
    result = decode_verdict({"verdict": "passed", "flags": '["tone could be warmer"]'})
    assert result == EvaluationResult(EvaluationStatus.PASSED, ("tone could be warmer",))


def test_qvd_04_extra_keys_are_ignored() -> None:
    result = decode_verdict({"verdict": "passed", "flags": "[]", "score": "97", "notes": "x"})
    assert result == EvaluationResult(EvaluationStatus.PASSED)


@pytest.mark.parametrize(
    "token", ["", "   ", "pending", "pass", "ok", "risk", "reject", "пройдено", "passed."]
)
def test_qvd_05_anything_but_an_exact_token_is_refused(token: str) -> None:
    with pytest.raises(QaVerdictError):
        decode_verdict({"verdict": token, "flags": '["reason"]'})


@pytest.mark.parametrize("fields", [{"flags": "[]"}, {"verdict": "passed"}, {}])
def test_qvd_06_a_missing_field_is_refused(fields: dict[str, str]) -> None:
    with pytest.raises(QaVerdictError):
        decode_verdict(fields)


@pytest.mark.parametrize(
    "raw_flags",
    ["", "not json", '"a flag"', '{"a": "b"}', "null", "[1]", '["ok", ""]', '["   "]', '[["a"]]'],
)
def test_qvd_07_malformed_flags_are_refused(raw_flags: str) -> None:
    with pytest.raises(QaVerdictError):
        decode_verdict({"verdict": "passed", "flags": raw_flags})


@pytest.mark.parametrize("token", ["flagged", "failed"])
def test_qvd_08_a_risk_verdict_without_a_flag_is_refused(token: str) -> None:
    with pytest.raises(QaVerdictError, match="at least one flag"):
        decode_verdict({"verdict": token, "flags": "[]"})


def test_qvd_09_a_malformed_answer_fails_closed_behind_the_port() -> None:
    run = make_run()
    artifact_id = candidate_artifact(run, "content")
    evaluator = DecodingEvaluator({"verdict": "looks fine", "flags": "[]"})
    with pytest.raises(QaVerdictError):
        evaluate_artifact(run, evaluator, artifact_id)
    assert [view.status for view in run.evaluations] == [EvaluationStatus.PENDING]
    review_id = run.open_human_review(artifact_id, by=CD)
    run.submit_review(review_id, ReviewStatus.APPROVED, by=REVIEWER)
    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(artifact_id, ArtifactStatus.APPROVED, by=CD)


def test_qvd_10_the_verdict_fields_are_pinned() -> None:
    assert QA_VERDICT_FIELDS == ("verdict", "flags")


# --- ContentDirector routing (ECD) -------------------------------------------------------


def test_ecd_01_passed_verdict_continues_to_completed() -> None:
    evaluator = RecordingEvaluator(EvaluationResult(EvaluationStatus.PASSED))
    run = run_director(evaluator)
    assert run.status is RunStatus.COMPLETED
    research, script = run.artifacts
    assert research.status is ArtifactStatus.DRAFT
    assert script.status is ArtifactStatus.CANDIDATE
    assert evaluator.seen == [FINAL_CONTENT]
    (evaluation,) = run.evaluations
    assert evaluation.artifact_ref == script.artifact_id
    assert evaluation.status is EvaluationStatus.PASSED
    assert run.human_reviews == ()


@pytest.mark.parametrize("verdict", RISK_VERDICTS)
def test_ecd_02_03_risk_verdict_escalates_to_the_human_and_never_completes(
    verdict: EvaluationStatus,
) -> None:
    run = run_director(RecordingEvaluator(EvaluationResult(verdict, ("unsupported claim",))))
    assert run.status is RunStatus.WAITING_HUMAN
    assert not any(isinstance(event, RunCompleted) for event in run.events)
    script = run.artifacts[-1]
    (review,) = run.human_reviews
    assert review.artifact_ref == script.artifact_id
    assert review.status is ReviewStatus.PENDING
    # Even a human Approve cannot push risky content further (fail closed).
    run.submit_review(review.review_id, ReviewStatus.APPROVED, by=REVIEWER)
    with pytest.raises(ArtifactQaNotPassedError):
        run.transition_artifact(script.artifact_id, ArtifactStatus.APPROVED, by=CD)
    assert run.artifact(script.artifact_id).status is ArtifactStatus.CANDIDATE


def test_ecd_04_qa_wired_but_no_candidate_fails_the_run() -> None:
    evaluator = RecordingEvaluator(EvaluationResult(EvaluationStatus.PASSED))
    run = run_director(evaluator, with_schemas=False)  # no Schema -> no Output -> no Artifact
    assert run.status is RunStatus.FAILED
    assert run.failure_reason == NO_CANDIDATE_REASON
    assert evaluator.seen == []
    assert run.evaluations == ()


def test_ecd_05_without_qa_the_flow_is_unchanged() -> None:
    run = run_director(None)
    assert run.status is RunStatus.COMPLETED
    assert run.evaluations == ()
    assert [artifact.status for artifact in run.artifacts] == [ArtifactStatus.DRAFT] * 2
