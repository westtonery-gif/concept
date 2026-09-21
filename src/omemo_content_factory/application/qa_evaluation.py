"""QA evaluation slice — invoke a QA evaluator on a candidate Artifact and persist its verdict.

The application-layer counterpart of ``task_execution`` for the ``WAITING_QA`` step (ADR-0018 §6).
Evaluation ownership follows Variant A (ADR-0013 §8), as Schema validation does: the **evaluator**
(a QA Agent behind a port) decides the verdict, the **application** invokes it, and **Run**
persists it through its root (``open_evaluation`` / ``record_evaluation``).

Fail closed by construction: there is no default verdict and no fallback. If the evaluator raises,
the exception propagates (PROJECT.md §10 — no swallowed errors) and the Evaluation stays
``PENDING``, which the Run's approval gate treats as "not passed". The one catch is for a
:class:`MeasuredEvaluatorError`: the measurements of the model calls the evaluator already made are
recorded first, then the same exception is re-raised (ADR-0036 §3).

An evaluator built on the structured LLM port turns the model's flat string fields into a verdict
only through :func:`decode_verdict` (ADR-0034); a malformed answer raises :class:`QaVerdictError`
and fails closed like any other evaluator failure. Every call it makes is attributed to the
Evaluation through the evaluator's ``evaluator_ref`` (ADR-0036).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.application.task_execution import AnalyticsMeasurement
from omemo_content_factory.domain.artifact import ArtifactId
from omemo_content_factory.domain.evaluation import EvaluationId, EvaluationStatus
from omemo_content_factory.domain.run import Actor, Run

QA_KIND = "qa"
"""The evaluation ``kind`` recorded for the QA gate (DOMAIN_MODEL.md §2.13: "вид оценки")."""

QA_VERDICT_FIELD = "verdict"
QA_FLAGS_FIELD = "flags"
QA_VERDICT_FIELDS = (QA_VERDICT_FIELD, QA_FLAGS_FIELD)
"""The structured fields of a QA answer — the QA role Schema's ``required_fields`` (ADR-0034)."""

_VERDICT_TOKENS = {
    status.value: status
    for status in (EvaluationStatus.PASSED, EvaluationStatus.FLAGGED, EvaluationStatus.FAILED)
}


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """What a QA evaluator judged for one candidate (application-layer, not the domain entity).

    ``verdict`` is the terminal outcome (``PASSED`` / ``FLAGGED`` / ``FAILED``; ``PENDING`` is
    rejected by the domain). ``flags`` are the remarks / risk flags shown to the human.
    ``analytics`` carries one measurement per completed model call, in call order (ADR-0036 §3);
    an evaluator that makes no model call keeps the empty default.
    """

    verdict: EvaluationStatus
    flags: tuple[str, ...] = ()
    analytics: tuple[AnalyticsMeasurement, ...] = ()


class MeasuredEvaluatorError(Exception):
    """An evaluator failure after model calls were made; it keeps their measurements (ADR-0036).

    :func:`record_verdict` records ``analytics`` and re-raises the same exception, so the failure
    still fails closed while the calls it cost are not lost.
    """

    def __init__(self, message: str, *, analytics: tuple[AnalyticsMeasurement, ...] = ()) -> None:
        super().__init__(message)
        self.analytics = analytics


class QaVerdictError(MeasuredEvaluatorError):
    """A QA answer's fields break the verdict contract (ADR-0034) — a failure, never a verdict."""


class QaCallError(MeasuredEvaluatorError):
    """The QA evaluator's model call itself failed (ADR-0036 §4) — a failure, never a verdict."""


def decode_verdict(fields: Mapping[str, str]) -> EvaluationResult:
    """Turn a QA answer's structured fields into an :class:`EvaluationResult` (ADR-0034).

    ``verdict`` must be exactly ``passed`` / ``flagged`` / ``failed`` (surrounding whitespace and
    case ignored); ``flags`` must be the JSON text of an array of non-blank strings, kept verbatim
    and in order. A risk verdict needs at least one flag. Other keys are ignored. Anything else
    raises :class:`QaVerdictError` — a malformed answer is never guessed into a verdict.
    """
    raw_verdict = fields.get(QA_VERDICT_FIELD)
    if raw_verdict is None:
        raise QaVerdictError(f"QA answer has no '{QA_VERDICT_FIELD}' field")
    verdict = _VERDICT_TOKENS.get(raw_verdict.strip().casefold())
    if verdict is None:
        raise QaVerdictError(
            f"'{QA_VERDICT_FIELD}' must be one of {sorted(_VERDICT_TOKENS)}, got {raw_verdict!r}"
        )

    raw_flags = fields.get(QA_FLAGS_FIELD)
    if raw_flags is None:
        raise QaVerdictError(f"QA answer has no '{QA_FLAGS_FIELD}' field")
    try:
        parsed: object = json.loads(raw_flags)
    except json.JSONDecodeError as exc:
        raise QaVerdictError(f"'{QA_FLAGS_FIELD}' is not valid JSON: {raw_flags!r}") from exc
    if not isinstance(parsed, list) or not all(
        isinstance(flag, str) and flag.strip() for flag in parsed
    ):
        raise QaVerdictError(
            f"'{QA_FLAGS_FIELD}' must be a JSON array of non-blank strings, got {raw_flags!r}"
        )
    flags = tuple(str(flag) for flag in parsed)

    if verdict is not EvaluationStatus.PASSED and not flags:
        raise QaVerdictError(f"a '{verdict.value}' verdict needs at least one flag")
    return EvaluationResult(verdict, flags)


class ArtifactEvaluator(Protocol):
    """Judges a candidate Artifact's content — the QA port (ADR-0018 §6).

    A single, explicit, injected dependency (PROJECT.md §6), like ``TaskExecutor``: tests supply a
    deterministic fake; the QA Agent (ROADMAP Stage 8) implements the same contract.
    ``evaluator_ref`` is the ``agent_ref`` of the answering role: every Evaluation it decides is
    opened with it, so its calls' metrics are attributed to that role (ADR-0036 §1, §3).
    """

    @property
    def evaluator_ref(self) -> str: ...

    def evaluate(self, content: str) -> EvaluationResult: ...


class ContextualArtifactEvaluator(ArtifactEvaluator, Protocol):
    """An ``ArtifactEvaluator`` that can also be shown what the content alone cannot (ADR-0068 §1).

    Additive: ``evaluate`` keeps its contract, and a caller that has context to give uses
    ``evaluate_in_context``. The context is derived from the Run by the caller and is not stored on
    the Evaluation — the Run already holds it.
    """

    def evaluate_in_context(self, content: str, context: str) -> EvaluationResult: ...


def evaluate_artifact(
    run: Run,
    evaluator: ArtifactEvaluator,
    artifact_id: ArtifactId,
    *,
    kind: str = QA_KIND,
) -> EvaluationId:
    """Evaluate one candidate Artifact of ``run`` and record the verdict, through the root.

    Acts in the Content Director role: opens a ``PENDING`` evaluation (the Artifact must be
    ``CANDIDATE``) naming the evaluator's role, hands the Artifact's content to ``evaluator`` and
    records its calls, verdict and flags. An evaluator exception propagates and leaves the
    evaluation ``PENDING`` (fail closed). Returns the new evaluation's id.
    """
    evaluation_id = run.open_evaluation(
        artifact_id, kind=kind, by=Actor.CONTENT_DIRECTOR, evaluator_ref=evaluator.evaluator_ref
    )
    record_verdict(run, evaluator, evaluation_id)
    return evaluation_id


def record_verdict(run: Run, evaluator: ArtifactEvaluator, evaluation_id: EvaluationId) -> None:
    """Ask ``evaluator`` about a ``PENDING`` Evaluation's Artifact and record the verdict.

    The half of :func:`evaluate_artifact` after the evaluation is opened, so a caller can commit the
    Run in between, or finish an evaluation a restart left ``PENDING`` (ADR-0026 §2-3). Every model
    call the evaluator reports is recorded first, through the Run root and attributed to the
    Evaluation (ADR-0036 §3). Fail closed as above: an evaluator exception propagates and the
    evaluation stays ``PENDING`` — a :class:`MeasuredEvaluatorError` after its calls are recorded.
    """
    _record(run, evaluator.evaluate, evaluation_id)


def evaluate_artifact_in_context(
    run: Run,
    evaluator: ContextualArtifactEvaluator,
    artifact_id: ArtifactId,
    context: str,
    *,
    kind: str = QA_KIND,
) -> EvaluationId:
    """:func:`evaluate_artifact`, with ``context`` shown to the evaluator (ADR-0068 §1)."""
    evaluation_id = run.open_evaluation(
        artifact_id, kind=kind, by=Actor.CONTENT_DIRECTOR, evaluator_ref=evaluator.evaluator_ref
    )
    record_verdict_in_context(run, evaluator, evaluation_id, context)
    return evaluation_id


def record_verdict_in_context(
    run: Run,
    evaluator: ContextualArtifactEvaluator,
    evaluation_id: EvaluationId,
    context: str,
) -> None:
    """:func:`record_verdict`, with ``context`` shown to the evaluator (ADR-0068 §1)."""
    _record(run, lambda content: evaluator.evaluate_in_context(content, context), evaluation_id)


def _record(run: Run, ask: Callable[[str], EvaluationResult], evaluation_id: EvaluationId) -> None:
    """The one path both entry points share: ask, record the calls, then the verdict."""
    artifact_ref = run.evaluation(evaluation_id).artifact_ref
    try:
        result = ask(run.artifact(artifact_ref).content)
    except MeasuredEvaluatorError as exc:
        _record_calls(run, evaluation_id, exc.analytics)
        raise
    _record_calls(run, evaluation_id, result.analytics)
    run.record_evaluation(
        evaluation_id, result.verdict, by=Actor.CONTENT_DIRECTOR, flags=result.flags
    )


def _record_calls(
    run: Run, evaluation_id: EvaluationId, analytics: tuple[AnalyticsMeasurement, ...]
) -> None:
    """Record each measured evaluator call through the Run root, in call order (ADR-0036 §3)."""
    for measurement in analytics:
        run.record_evaluation_analytics(
            evaluation_id,
            provider=measurement.provider,
            model=measurement.model,
            token_usage=measurement.token_usage,
            cost=measurement.cost,
            time_range=measurement.time_range,
            prompt_ref=measurement.prompt_ref,
            by=Actor.CONTENT_DIRECTOR,
        )
