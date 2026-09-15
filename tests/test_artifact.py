"""Behavioural tests for the Artifact entity (``omemo_content_factory.domain.artifact``).

Artifact is driven **only through the Run aggregate root** (``create_artifact`` /
``transition_artifact`` / read-only ``artifacts`` / ``artifact``) and, end to end, through the
``ContentDirector``. Tests reference the rules of ADR-0006 / DOMAIN_MODEL.md §2.12. No mocks,
sleep or randomness; a deterministic fake executor stands in for real work.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from omemo_content_factory.application.content_director import ContentDirector, TaskRequest
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.domain.artifact import (
    ArtifactCreated,
    ArtifactStatus,
    DuplicateArtifactError,
    InvalidArtifactTransitionError,
)
from omemo_content_factory.domain.run import Actor, Run, RunStatus, UnauthorizedActorError
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.task import TaskStatus


def _open_schema() -> Schema:
    """An ACTIVE Schema with no required fields — any result validates VALID (for wiring tests)."""
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=[]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


RUN_ID = "run-art-0001"
BRIEF_REF = "brief-0001"
WORKFLOW_REF = "workflow@v1"
STEP_REF = "step-1"
AGENT_REF = "writer@v1"
TASK_INPUT = "approved brief body"
SCHEMA_REF = "content-output@v1"
PAYLOAD = "generated content"
CD = Actor.CONTENT_DIRECTOR

# Edges that do NOT raise InvalidArtifactTransitionError, per the reachable source states
# (ADR-0006 §4 + ADR-0007 §6 + ADR-0019 §3). Two of them are valid edges guarded by the Run root
# rather than by the transition table, so they raise a *different* domain error and are excluded
# from the "structurally forbidden" set tested here: ``CANDIDATE -> APPROVED`` (gated by an
# approving Human Review and a passing QA verdict — see test_human_review.py / test_evaluation.py)
# and ``-> SUPERSEDED`` (reachable only by creating the next version — see
# test_artifact_versioning.py).
_ALLOWED_BY_SPEC: dict[ArtifactStatus, set[ArtifactStatus]] = {
    ArtifactStatus.DRAFT: {ArtifactStatus.CANDIDATE, ArtifactStatus.SUPERSEDED},
    ArtifactStatus.CANDIDATE: {
        ArtifactStatus.APPROVED,
        ArtifactStatus.REJECTED,
        ArtifactStatus.SUPERSEDED,
    },
}
_FORBIDDEN_TRANSITIONS: list[tuple[ArtifactStatus, ArtifactStatus]] = [
    (source, target)
    for source in (ArtifactStatus.DRAFT, ArtifactStatus.CANDIDATE)
    for target in ArtifactStatus
    if target not in _ALLOWED_BY_SPEC[source]
]


@dataclass(frozen=True, slots=True)
class OutputtingExecutor:
    """Deterministic executor that always succeeds with an output and a schema_ref."""

    def execute(self, task_input: str) -> ExecutionResult:
        return ExecutionResult(
            succeeded=True, output=f"[gen] {task_input}", schema_ref="s@v1", payload_fields={}
        )


def make_run() -> Run:
    """A fresh Run in CREATED."""
    return Run.create(
        run_id=RUN_ID,
        content_brief_ref=BRIEF_REF,
        workflow_version_ref=WORKFLOW_REF,
    )


def output_on_run(run: Run) -> str:
    """Drive a Task to SUCCEEDED on ``run``, record its Output, and return the output id."""
    task_id = run.open_task(
        workflow_step_ref=STEP_REF, agent_ref=AGENT_REF, task_input=TASK_INPUT, by=CD
    )
    run.transition_task(task_id, TaskStatus.RUNNING, by=CD)
    run.transition_task(task_id, TaskStatus.SUCCEEDED, by=CD)
    return run.record_output(task_id, payload=PAYLOAD, schema_ref=SCHEMA_REF, by=CD)


def artifact_in(run: Run, state: ArtifactStatus) -> str:
    """Create an Artifact and drive it to ``state`` (DRAFT or CANDIDATE); return its id."""
    artifact_id = run.create_artifact(output_on_run(run), kind="draft", by=CD)
    if state is ArtifactStatus.CANDIDATE:
        run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, by=CD)
    return artifact_id


# --- Creation from an Output -------------------------------------------------------------


def test_create_artifact_from_output_is_draft_with_provenance() -> None:
    """A created Artifact is DRAFT, takes its content from the Output, and records provenance."""
    run = make_run()
    output_id = output_on_run(run)
    artifact_id = run.create_artifact(output_id, kind="draft", by=CD)
    artifact = run.artifact(artifact_id)
    assert artifact.artifact_id == artifact_id
    assert artifact.run_id == run.run_id
    assert artifact.output_ref == output_id
    assert artifact.kind == "draft"
    assert artifact.content == PAYLOAD
    assert artifact.version == 1
    assert artifact.status is ArtifactStatus.DRAFT


def test_create_artifact_emits_artifact_created_in_the_run_log() -> None:
    """Creating an Artifact emits ArtifactCreated (with provenance) in the Run's event log."""
    run = make_run()
    output_id = output_on_run(run)
    artifact_id = run.create_artifact(output_id, kind="draft", by=CD)
    last = run.events[-1]
    assert isinstance(last, ArtifactCreated)
    assert last.run_id == run.run_id
    assert last.artifact_id == artifact_id
    assert last.output_ref == output_id
    assert last.kind == "draft"


def test_an_output_produces_only_one_artifact() -> None:
    """A second Artifact from the same Output is rejected (1:1, ADR-0006 §5)."""
    run = make_run()
    output_id = output_on_run(run)
    run.create_artifact(output_id, kind="draft", by=CD)
    with pytest.raises(DuplicateArtifactError):
        run.create_artifact(output_id, kind="draft", by=CD)


def test_create_artifact_requires_an_existing_output() -> None:
    """An Artifact cannot be created from an unknown Output (ADR-0006 §5)."""
    run = make_run()
    with pytest.raises(KeyError):
        run.create_artifact("no-such-output", kind="draft", by=CD)


def test_only_content_director_can_create_an_artifact() -> None:
    """A non-Content-Director actor cannot create an Artifact (ADR-0006 §6)."""
    run = make_run()
    output_id = output_on_run(run)
    with pytest.raises(UnauthorizedActorError):
        run.create_artifact(output_id, kind="draft", by=Actor.AGENT)
    assert len(run.artifacts) == 0


# --- Lifecycle ---------------------------------------------------------------------------


def test_artifact_transitions_draft_to_candidate() -> None:
    """The wired lifecycle edge DRAFT -> CANDIDATE works through the root (ADR-0006 §4)."""
    run = make_run()
    artifact_id = run.create_artifact(output_on_run(run), kind="draft", by=CD)
    run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, by=CD)
    assert run.artifact(artifact_id).status is ArtifactStatus.CANDIDATE


@pytest.mark.parametrize(("source", "target"), _FORBIDDEN_TRANSITIONS)
def test_forbidden_artifact_transitions_are_rejected(
    source: ArtifactStatus, target: ArtifactStatus
) -> None:
    """Every edge outside the allowed-transitions table is rejected (ADR-0006 §4)."""
    run = make_run()
    artifact_id = artifact_in(run, source)
    with pytest.raises(InvalidArtifactTransitionError):
        run.transition_artifact(artifact_id, target, by=CD)
    assert run.artifact(artifact_id).status is source


def test_only_content_director_can_transition_an_artifact() -> None:
    """A non-Content-Director actor cannot change an Artifact's status (ADR-0006 §6)."""
    run = make_run()
    artifact_id = run.create_artifact(output_on_run(run), kind="draft", by=CD)
    with pytest.raises(UnauthorizedActorError):
        run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, by=Actor.AGENT)
    assert run.artifact(artifact_id).status is ArtifactStatus.DRAFT


# --- End to end through the ContentDirector ----------------------------------------------


def test_workflow_creates_one_artifact_per_output_with_provenance() -> None:
    """A full workflow yields one Artifact per produced Output, each tracing back to its Output."""
    schemas = {"writer@v1": _open_schema(), "editor@v1": _open_schema()}
    director = ContentDirector(OutputtingExecutor(), schemas)
    run = Run.create(
        run_id="run-art-wf", content_brief_ref=BRIEF_REF, workflow_version_ref=WORKFLOW_REF
    )
    requests = [
        TaskRequest("step-write", "writer@v1", "brief", artifact_kind="draft"),
        TaskRequest("step-edit", "editor@v1", "draft", artifact_kind="edited"),
    ]
    director.execute(run, requests)
    assert run.status is RunStatus.COMPLETED
    artifacts = run.artifacts
    assert [artifact.kind for artifact in artifacts] == ["draft", "edited"]
    # Provenance: every Artifact references an existing Output of the Run.
    output_ids = {view.output.output_id for view in run.tasks if view.output is not None}
    assert {artifact.output_ref for artifact in artifacts} == output_ids
    # The Content Director chains Outputs into the next Task's input, so the second step's
    # content is derived from the first step's Output ("[gen] brief"), not its static input.
    assert {artifact.content for artifact in artifacts} == {"[gen] brief", "[gen] [gen] brief"}
