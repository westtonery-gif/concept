"""Required, committed processing between cutting and clip QA (ADR-0095)."""

from __future__ import annotations

import json

from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.adapters.video_processor import (
    VideoProcessingError,
    VideoProcessingRequest,
    VideoProcessor,
)
from omemo_content_factory.application.clip_format import ClipFormatLimits, check_clip_format
from omemo_content_factory.domain.artifact import ArtifactStatus, ArtifactView
from omemo_content_factory.domain.run import Actor, Run
from omemo_content_factory.domain.task import TaskStatus, TaskView

PROCESS_STEP_REF = "process-video"
PROCESS_AGENT_REF = "video_processor@v1"
PROCESS_SCHEMA_REF = "processed-clip@v1"
PROCESS_FAILED_REASON = "VIDEO_PROCESSING_FAILED"


def processed_destination(source: str) -> str:
    stem, dot, _suffix = source.rpartition(".")
    return f"{stem if dot else source}.unique.mp4"


def is_processed_artifact(run: Run, artifact: ArtifactView) -> bool:
    return any(
        task.workflow_step_ref == PROCESS_STEP_REF
        and task.status is TaskStatus.SUCCEEDED
        and task.output is not None
        and task.output.output_id == artifact.output_ref
        for task in run.tasks
    )


def process_clips(
    run: Run, store: RunStore, processor: VideoProcessor, limits: ClipFormatLimits
) -> tuple[str, ...]:
    """Only processed outputs become candidates; failed cuts are independent."""
    failures: list[str] = []
    for raw in tuple(run.tasks):
        if raw.workflow_step_ref != "render-clip" or raw.status is not TaskStatus.SUCCEEDED:
            continue
        assert raw.output is not None
        # Legacy artifacts are never silently replaced after their review/approval.
        if any(a.output_ref == raw.output.output_id for a in run.artifacts):
            continue
        task = _processing_task(run, raw.output.output_id)
        if task is not None and task.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
            continue
        task_id = _process_one(run, store, processor, limits, raw, task)
        if task_id is not None:
            failures.append(task_id)
    return tuple(failures)


def _process_one(
    run: Run,
    store: RunStore,
    processor: VideoProcessor,
    limits: ClipFormatLimits,
    raw: TaskView,
    task: TaskView | None,
) -> str | None:
    assert raw.output is not None
    payload = json.loads(raw.output.payload)
    if task is None:
        task_id = run.open_task(
            PROCESS_STEP_REF,
            PROCESS_AGENT_REF,
            json.dumps(
                {
                    "raw_output_ref": raw.output.output_id,
                    "source_path": payload["path"],
                    "destination": processed_destination(payload["path"]),
                }
            ),
            Actor.CONTENT_DIRECTOR,
        )
        run.transition_task(task_id, TaskStatus.RUNNING, Actor.CONTENT_DIRECTOR)
        store.save(run)
        task = next(t for t in run.tasks if t.task_id == task_id)
    stored = json.loads(task.task_input)
    try:
        processed = processor.process(
            VideoProcessingRequest(
                request_id=task.task_id,
                source_path=stored["source_path"],
                destination=stored["destination"],
            )
        )
        violations = check_clip_format(processed.clip, limits=limits)
        if violations:
            raise VideoProcessingError(f"processed clip format violation: {violations[0]}")
    except VideoProcessingError:
        run.transition_task(
            task.task_id, TaskStatus.FAILED, Actor.CONTENT_DIRECTOR, reason=PROCESS_FAILED_REASON
        )
        store.save(run)
        return task.task_id
    payload.update(
        path=processed.clip.path,
        video_processing={
            "source_sha256": processed.source_sha256,
            "output_sha256": processed.output_sha256,
            "report_path": processed.report_path,
        },
    )
    run.transition_task(task.task_id, TaskStatus.SUCCEEDED, Actor.CONTENT_DIRECTOR)
    output_id = run.record_output(
        task.task_id,
        payload=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        schema_ref=PROCESS_SCHEMA_REF,
        by=Actor.CONTENT_DIRECTOR,
    )
    artifact_id = run.create_artifact(output_id, kind="clip", by=Actor.CONTENT_DIRECTOR)
    run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, Actor.CONTENT_DIRECTOR)
    store.save(run)
    return None


def _processing_task(run: Run, raw_output_ref: str) -> TaskView | None:
    return next(
        (
            task
            for task in run.tasks
            if task.workflow_step_ref == PROCESS_STEP_REF
            and json.loads(task.task_input)["raw_output_ref"] == raw_output_ref
        ),
        None,
    )
