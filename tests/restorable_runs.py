"""Builders of Runs worth preserving, shared by the restoration and storage tests (ADR-0024).

Not a test module: it holds no tests. It holds Runs driven through the public Run API only, the
way the Content Director drives them, into states whose snapshots are then restored or stored.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from omemo_content_factory.domain.analytics import Cost, TimeRange, TokenUsage
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Actor, ReworkPolicy, Run, RunStatus
from omemo_content_factory.domain.task import TaskRetryPolicy, TaskStatus

CD = Actor.CONTENT_DIRECTOR
REVIEWER = Actor.HUMAN_REVIEWER
RUN_ID = "run-rst-0001"
V2 = f"{RUN_ID}-artifact-2"
"""The second version of the script in :func:`busy_run`: a passed ``CANDIDATE``."""
V2_REVIEW = f"{RUN_ID}-review-2"
"""The still pending Human Review of :data:`V2` in :func:`busy_run`."""

_MOSCOW = timezone(timedelta(hours=3))


def new_run(rework_policy: ReworkPolicy | None = None) -> Run:
    """A fresh Run in ``CREATED``."""
    return Run.create(
        run_id=RUN_ID,
        content_brief_ref="brief-rst",
        workflow_version_ref="workflow@v1",
        rework_policy=rework_policy,
    )


def drive(run: Run, *path: RunStatus) -> None:
    """Move ``run`` along ``path`` as the Content Director."""
    for status in path:
        run.transition(status, by=CD, reason="провайдер недоступен")


def succeeded_task(
    run: Run, *, step: str, payload: str, agent: str = "script_writer@v1"
) -> tuple[str, str]:
    """Open a Task, run it to ``SUCCEEDED`` and record its Output; return both ids."""
    task_id = run.open_task(step, agent, "бриф", by=CD)
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=CD)
    return task_id, run.record_output(task_id, payload=payload, schema_ref=f"{step}@v1", by=CD)


def record_call(run: Run, task_id: str, *, cost: str = "0.0123") -> str:
    """Record the metrics of one call for ``task_id``: an exact cost, a non-UTC time range."""
    started = datetime(2026, 9, 15, 10, 0, tzinfo=_MOSCOW)
    return run.record_analytics(
        task_id,
        provider="anthropic",
        model="model-a",
        token_usage=TokenUsage(input_tokens=1200, output_tokens=345),
        cost=Cost(amount=Decimal(cost), currency="USD"),
        time_range=TimeRange(
            started_at=started, finished_at=started + timedelta(seconds=4, microseconds=250)
        ),
        by=CD,
        prompt_ref="prompt@v1",
    )


def evaluate(
    run: Run, artifact_id: str, verdict: EvaluationStatus, flags: tuple[str, ...] = ()
) -> str:
    """Open a QA Evaluation of ``artifact_id`` and record ``verdict``; return its id."""
    evaluation_id = run.open_evaluation(artifact_id, kind="qa", by=CD)
    run.record_evaluation(evaluation_id, verdict, by=CD, flags=flags)
    return evaluation_id


def busy_run() -> Run:
    """A Run in ``WAITING_HUMAN`` holding everything a real production accumulates.

    - task-1, research: retried once under a custom retry policy, two analytics records, an Output;
    - task-2 skipped, task-3 failed with a reason;
    - task-4: script v1 (artifact-1), flagged by QA, then a ``CHANGES_REQUESTED`` review (review-1);
    - one rework, under a custom rework policy;
    - task-5: script v2 (artifact-2, supersedes v1), passed by QA, with a pending review (review-2).

    Every Task is terminal, so the Run can still complete once v2 is approved.
    """
    run = new_run(ReworkPolicy(max_rework_iterations=2))
    drive(run, RunStatus.QUEUED, RunStatus.RUNNING)

    research = run.open_task(
        "research", "content_researcher@v1", "бриф", by=CD, retry_policy=TaskRetryPolicy(2)
    )
    run.transition_task(research, TaskStatus.RUNNING, by=CD)
    record_call(run, research, cost="0.0100")
    run.transition_task(research, TaskStatus.RUNNING, by=CD)
    record_call(run, research)
    run.transition_task(research, TaskStatus.SUCCEEDED, by=CD)
    run.record_output(research, payload="Тезисы о сне.", schema_ref="research@v1", by=CD)

    skipped = run.open_task("illustrate", "illustrator@v1", "бриф", by=CD)
    run.transition_task(skipped, TaskStatus.SKIPPED, by=CD)
    failed = run.open_task("fact_check", "fact_checker@v1", "бриф", by=CD)
    run.transition_task(failed, TaskStatus.RUNNING, by=CD)
    run.transition_task(failed, TaskStatus.FAILED, by=CD, reason="таймаут провайдера")

    _, draft = succeeded_task(run, step="script", payload="Сценарий, версия 1.")
    v1 = run.create_artifact(draft, kind="script", by=CD)
    run.transition_artifact(v1, ArtifactStatus.CANDIDATE, by=CD)
    drive(run, RunStatus.WAITING_QA)
    evaluate(run, v1, EvaluationStatus.FLAGGED, flags=("источник не указан",))
    review = run.open_human_review(v1, by=CD)
    run.submit_review(review, ReviewStatus.CHANGES_REQUESTED, by=REVIEWER, reason="Дать источник.")
    drive(run, RunStatus.RUNNING)

    _, reworked = succeeded_task(run, step="script", payload="Сценарий, версия 2.")
    v2 = run.create_artifact_version(v1, reworked, by=CD)
    run.transition_artifact(v2, ArtifactStatus.CANDIDATE, by=CD)
    drive(run, RunStatus.WAITING_QA)
    evaluate(run, v2, EvaluationStatus.PASSED)
    drive(run, RunStatus.WAITING_HUMAN)
    run.open_human_review(v2, by=CD)
    return run
