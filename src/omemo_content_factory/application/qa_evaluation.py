"""QA evaluation slice — invoke a QA evaluator on a candidate Artifact and persist its verdict.

The application-layer counterpart of ``task_execution`` for the ``WAITING_QA`` step (ADR-0018 §6).
Evaluation ownership follows Variant A (ADR-0013 §8), as Schema validation does: the **evaluator**
(a QA Agent behind a port) decides the verdict, the **application** invokes it, and **Run**
persists it through its root (``open_evaluation`` / ``record_evaluation``).

Fail closed by construction: this slice never catches. If the evaluator raises, the exception
propagates (PROJECT.md §10 — no swallowed errors) and the Evaluation stays ``PENDING``, which the
Run's approval gate treats as "not passed". There is no default verdict and no fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.domain.artifact import ArtifactId
from omemo_content_factory.domain.evaluation import EvaluationId, EvaluationStatus
from omemo_content_factory.domain.run import Actor, Run

QA_KIND = "qa"
"""The evaluation ``kind`` recorded for the QA gate (DOMAIN_MODEL.md §2.13: "вид оценки")."""


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """What a QA evaluator judged for one candidate (application-layer, not the domain entity).

    ``verdict`` is the terminal outcome (``PASSED`` / ``FLAGGED`` / ``FAILED``; ``PENDING`` is
    rejected by the domain). ``flags`` are the remarks / risk flags shown to the human.
    """

    verdict: EvaluationStatus
    flags: tuple[str, ...] = ()


class ArtifactEvaluator(Protocol):
    """Judges a candidate Artifact's content — the QA port (ADR-0018 §6).

    A single, explicit, injected dependency (PROJECT.md §6), like ``TaskExecutor``: tests supply a
    deterministic fake; the QA Agent (ROADMAP Stage 8) implements the same contract.
    """

    def evaluate(self, content: str) -> EvaluationResult: ...


def evaluate_artifact(
    run: Run,
    evaluator: ArtifactEvaluator,
    artifact_id: ArtifactId,
    *,
    kind: str = QA_KIND,
) -> EvaluationId:
    """Evaluate one candidate Artifact of ``run`` and record the verdict, through the root.

    Acts in the Content Director role: opens a ``PENDING`` evaluation (the Artifact must be
    ``CANDIDATE``), hands the Artifact's content to ``evaluator`` and records its verdict and
    flags. An evaluator exception propagates and leaves the evaluation ``PENDING`` (fail closed).
    Returns the new evaluation's id.
    """
    evaluation_id = run.open_evaluation(artifact_id, kind=kind, by=Actor.CONTENT_DIRECTOR)
    record_verdict(run, evaluator, evaluation_id)
    return evaluation_id


def record_verdict(run: Run, evaluator: ArtifactEvaluator, evaluation_id: EvaluationId) -> None:
    """Ask ``evaluator`` about a ``PENDING`` Evaluation's Artifact and record the verdict.

    The half of :func:`evaluate_artifact` after the evaluation is opened, so a caller can commit the
    Run in between, or finish an evaluation a restart left ``PENDING`` (ADR-0026 §2-3). Fail closed
    as above: an evaluator exception propagates and the evaluation stays ``PENDING``.
    """
    artifact_ref = run.evaluation(evaluation_id).artifact_ref
    result = evaluator.evaluate(run.artifact(artifact_ref).content)
    run.record_evaluation(
        evaluation_id, result.verdict, by=Actor.CONTENT_DIRECTOR, flags=result.flags
    )
