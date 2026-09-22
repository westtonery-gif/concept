"""One invocation of the clipping department (ADR-0059, CLIPPING_SPEC §8).

An episode is **one Run** whose clips are its Artifacts. This module orchestrates it **beside**
``ContentDirector``, never through it: the Director's contract is a *declared* Workflow whose Tasks
are matched by position and which ends in one candidate (ADR-0026), and a fan-out of unknown width
is a different shape. Bending the Director into it would change the core every other role shares,
which the Conventions forbid — so this is an additive module, as ``BriefProduction`` is.

Two rules of the Director are deliberately **not** copied:

- **Fail-fast across steps.** ADR-0031 stops a sequential plan at its first non-successful Task
  because each step feeds the next. Clips are independent, so one clip that cannot be rendered
  fails **its own** Task and the rest of the episode continues.
- **One candidate.** Every clip gets its own Artifact, verdict and human gate — thirteen
  independent fail-closed gates, which the Run aggregate already supports unchanged (ADR-0059 §1).

What **is** copied is ADR-0026 §2's commit discipline: the Run is saved before each outside call
and again with its result, so a stored Run never holds a half-recorded step and a crash resumes
instead of repeating.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisher,
    ClipPublisherError,
    PublishRequest,
    PublishState,
    PublishStatus,
)
from omemo_content_factory.adapters.clip_renderer import (
    ClipRenderer,
    ClipRendererError,
    ClipRenderRequest,
)
from omemo_content_factory.adapters.episode_board import EpisodeBoard, IncomingEpisode
from omemo_content_factory.adapters.episode_source import (
    EpisodeSource,
    EpisodeSourceError,
    LocatedEpisode,
)
from omemo_content_factory.adapters.footage_index import (
    FootageIndex,
    FootageIndexError,
    IndexedFootage,
)
from omemo_content_factory.adapters.review_desk import PostDraft, ReviewDesk
from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.application.clip_format import ClipFormatLimits, check_clip_format
from omemo_content_factory.application.clip_plan import PlannedClip, plan_clips
from omemo_content_factory.application.clip_review import (
    POST_SCHEMA_REF,
    POST_STEP_REF,
    latest_post,
    open_clip_reviews,
    post_task_input,
    publish_clip_reviews,
    take_clip_decisions,
)
from omemo_content_factory.application.footage_record import (
    FOOTAGE_SCHEMA_REF,
    FootageRecordError,
    episode_transcript,
    footage_from_payload,
    footage_to_payload,
)
from omemo_content_factory.application.qa_evaluation import (
    ContextualArtifactEvaluator,
    evaluate_artifact_in_context,
)
from omemo_content_factory.application.review_decision import FetchedDecision
from omemo_content_factory.application.review_publication import latest_qa
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import (
    TaskExecutor,
    finish_task,
    start_task,
)
from omemo_content_factory.domain.artifact import ArtifactStatus, ArtifactView
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunStatus
from omemo_content_factory.domain.task import TaskStatus, TaskView

__all__ = [
    "CLIP_KIND",
    "ClipInvocation",
    "ClipProduction",
    "ClipProductionError",
    "ClipSettings",
    "ClipTally",
    "PostWriting",
    "Posting",
    "run_id_for_episode",
]

CLIP_KIND = "clip"
"""The Artifact ``kind`` every clip carries."""

CLIP_SCHEMA_REF = "clip-artifact@v1"
"""The Output's opaque reference.

A clip's payload is produced by arithmetic and a renderer, not by a model, so there is no
inter-agent message to validate (ADR-0008 is about Schema-checked agent output). The reference
names the shape for the trace; nothing re-derives validity from it.
"""

WORKFLOW_REF = "episode-to-clips@v1"
STEP_REF = "render-clip"
AGENT_REF = "clip_renderer@v1"
"""The clip step names no LLM role: rendering is mechanical (ADR-0058 §4)."""

POST_WRITER_REF = "clip_post_writer@v1"
REVIEWER_REF = "human_reviewer"
"""The ``agent_ref`` of a post text the reviewer edited on the review page (ADR-0072 §4)."""

PUBLISH_STEP_REF = "publish-clip"
PUBLISHER_REF = "clip_publisher@v1"
PUBLICATION_SCHEMA_REF = "clip-publication@v1"
NO_POST_TEXT_REASON = "NO_POST_TEXT"
PUBLISH_FAILED_REASON = "PUBLISH_FAILED"

INDEX_STEP_REF = "index-footage"
INDEX_AGENT_REF = "footage_index@v1"
"""The index step: recorded once per Run, so QA and resumption read it back (ADR-0068 §2)."""

SOURCE_MISSING_REASON = "EPISODE_SOURCE_MISSING"
INDEX_FAILED_REASON = "FOOTAGE_INDEX_FAILED"
RENDER_FAILED_REASON = "CLIP_RENDER_FAILED"
FORMAT_REASON = "CLIP_FORMAT_VIOLATION"
NO_INDEX_ERROR = "NO_FOOTAGE_INDEX"


class ClipProductionError(Exception):
    """The department was asked for something it cannot do (application error, not domain)."""


def run_id_for_episode(episode_ref: str, /) -> str:
    """The Run id an episode always produces under, so a re-invocation resumes it."""
    return f"run-episode-{episode_ref}"


@dataclass(frozen=True, slots=True)
class ClipSettings:
    """Production parameters. Configuration, never columns on the board (ADR-0055 §2)."""

    chunk_ms: int
    max_ms: int
    pause_tolerance_ms: int
    limits: ClipFormatLimits
    destination_template: str = "{episode_ref}-{index:02d}.mp4"


@dataclass(frozen=True, slots=True)
class PostWriting:
    """The post-text role, compiled: its executor and the Schema its Output is validated against."""

    executor: TaskExecutor
    schema_binding: SchemaBinding


@dataclass(frozen=True, slots=True)
class Posting:
    """Where approved clips are posted: the publisher and the platforms it posts to (ADR-0073)."""

    publisher: ClipPublisher
    platforms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClipTally:
    """Where an episode's clips stand, counted from the Run (task 24.4).

    ``completed`` is a Run status, not a result: a Run whose every clip failed QA completes too,
    because a clip is not the Run's fate (ADR-0059 §3). These counts are what says whether anything
    is actually ready. ``render_failed`` are clips that never became an Artifact; the verdicts are
    each Artifact's latest QA answer, ``unjudged`` those still without one.
    """

    render_failed: int = 0
    passed: int = 0
    flagged: int = 0
    failed: int = 0
    unjudged: int = 0
    approved: int = 0
    posted: int = 0


@dataclass(frozen=True, slots=True)
class ClipInvocation:
    """What one invocation did, as data rather than printing (the ADR-0048 shape)."""

    episode_ref: str
    run_id: str | None = None
    status: RunStatus | None = None
    clips: int = 0
    failed_clips: tuple[str, ...] = ()
    published: tuple[str, ...] = ()
    decisions: tuple[FetchedDecision, ...] = ()
    approved: tuple[str, ...] = ()
    qa_error: str | None = None
    publish_error: str | None = None
    decision_error: str | None = None
    failure_reason: str | None = None
    tally: ClipTally = ClipTally()
    post_error: str | None = None
    posted: tuple[str, ...] = ()
    posting_error: str | None = None


@dataclass
class _Outcome:
    """Mutable accumulator the invocation fills; frozen into a ``ClipInvocation`` at the end."""

    failed_clips: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    decisions: list[FetchedDecision] = field(default_factory=list)
    approved: list[str] = field(default_factory=list)
    qa_error: str | None = None
    publish_error: str | None = None
    decision_error: str | None = None
    post_error: str | None = None
    posted: list[str] = field(default_factory=list)
    posting_error: str | None = None


class ClipProduction:
    """Produces one episode's clips, from the board to a human decision on each."""

    def __init__(
        self,
        store: RunStore,
        board: EpisodeBoard,
        source: EpisodeSource,
        index: FootageIndex,
        renderer: ClipRenderer,
        evaluator: ContextualArtifactEvaluator,
        *,
        settings: ClipSettings,
        desk: ReviewDesk | None = None,
        post_writer: PostWriting | None = None,
        posting: Posting | None = None,
    ) -> None:
        self._store = store
        self._board = board
        self._source = source
        self._index = index
        self._renderer = renderer
        self._evaluator = evaluator
        self._settings = settings
        self._desk = desk
        self._post_writer = post_writer
        self._posting = posting

    @property
    def has_desk(self) -> bool:
        """Whether a review desk is configured; without one, reviews stay in the Run store."""
        return self._desk is not None

    def invoke(self, episode_ref: str, /) -> ClipInvocation:
        """Advance ``episode_ref`` as far as this invocation can, and say what happened."""
        run_id = run_id_for_episode(episode_ref)
        run = self._store.load(run_id)
        if run is None:
            episode = self._board.fetch_episode(episode_ref)
            if episode is None:
                return ClipInvocation(episode_ref=episode_ref)
            run = Run.create(
                run_id=run_id,
                content_brief_ref=episode_ref,
                workflow_version_ref=WORKFLOW_REF,
            )
            run.transition(RunStatus.QUEUED, Actor.CONTENT_DIRECTOR)
            self._store.save(run)
        elif run.content_brief_ref != episode_ref:
            raise ClipProductionError(
                f"run {run_id} belongs to episode {run.content_brief_ref}, not {episode_ref}"
            )

        if run.status in (RunStatus.COMPLETED, RunStatus.FAILED):
            return ClipInvocation(
                episode_ref=episode_ref,
                run_id=run_id,
                status=run.status,
                clips=len(run.artifacts),
                tally=_tally(run),
            )

        outcome = _Outcome()
        if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            reason = self._produce(run, episode_ref, outcome)
            if reason is not None:
                return ClipInvocation(
                    episode_ref=episode_ref,
                    run_id=run_id,
                    status=run.status,
                    failure_reason=reason,
                    tally=_tally(run),
                )
        self._judge(run, outcome)
        self._review(run, outcome)
        return ClipInvocation(
            episode_ref=episode_ref,
            run_id=run_id,
            status=run.status,
            clips=len(run.artifacts),
            failed_clips=tuple(outcome.failed_clips),
            published=tuple(outcome.published),
            decisions=tuple(outcome.decisions),
            approved=tuple(outcome.approved),
            qa_error=outcome.qa_error,
            publish_error=outcome.publish_error,
            decision_error=outcome.decision_error,
            tally=_tally(run),
            post_error=outcome.post_error,
            posted=tuple(outcome.posted),
            posting_error=outcome.posting_error,
        )

    # --- production ---------------------------------------------------------------------

    def _produce(self, run: Run, episode_ref: str, outcome: _Outcome) -> str | None:
        """Cut every clip. Returns a failure reason when the **episode** cannot be produced."""
        episode = self._board.fetch_episode(episode_ref)
        if episode is None:
            raise ClipProductionError(f"episode {episode_ref!r} is no longer ready to clip")
        located = self._source.locate(episode.source_ref)
        if located is None:
            return self._fail(run, SOURCE_MISSING_REASON)
        if run.status is RunStatus.QUEUED:
            run.transition(RunStatus.RUNNING, Actor.CONTENT_DIRECTOR)
            self._store.save(run)
        footage = self._footage(run, located)
        if footage is None:
            return INDEX_FAILED_REASON

        plan = plan_clips(
            footage,
            episode_ref=episode_ref,
            mode=episode.mode,
            chunk_ms=self._settings.chunk_ms,
            max_ms=self._settings.max_ms,
            pause_tolerance_ms=self._settings.pause_tolerance_ms,
        )

        # Resumption: the plan is a pure function of the footage (CLP-09), so re-planning gives
        # exactly the clips a crashed invocation was producing. A Task already recorded for a clip
        # is its committed trace — terminal ones are reused, and one left ``RUNNING`` is finished
        # on its stored input rather than opened a second time (ADR-0026 §3).
        recorded = _tasks_by_clip(run)
        for clip in plan.clips:
            task = recorded.get(clip.index)
            if task is not None and task.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
                continue
            self._render_one(
                run, episode, located, clip, outcome, task_id=None if task is None else task.task_id
            )
        run.transition(RunStatus.WAITING_QA, Actor.CONTENT_DIRECTOR)
        self._store.save(run)
        return None

    def _footage(self, run: Run, located: LocatedEpisode) -> IndexedFootage | None:
        """The episode's index: read back if recorded, else indexed and recorded (ADR-0068 §2).

        Returns ``None`` after failing the Run when the episode cannot be indexed. A Task a crashed
        invocation left ``RUNNING`` is finished rather than opened a second time (ADR-0026 §3).
        """
        task = _index_task(run)
        if task is not None and task.status is TaskStatus.SUCCEEDED and task.output is not None:
            return footage_from_payload(task.output.payload)
        if task is None:
            task_input = json.dumps({"source_ref": located.source_ref}, ensure_ascii=False)
            task_id = run.open_task(
                INDEX_STEP_REF, INDEX_AGENT_REF, task_input, Actor.CONTENT_DIRECTOR
            )
            run.transition_task(task_id, TaskStatus.RUNNING, Actor.CONTENT_DIRECTOR)
            self._store.save(run)  # committed before the outside call (ADR-0026 §2)
        else:
            task_id = task.task_id
        try:
            footage = self._index.index(located)
        except (FootageIndexError, EpisodeSourceError):
            run.transition_task(
                task_id, TaskStatus.FAILED, Actor.CONTENT_DIRECTOR, reason=INDEX_FAILED_REASON
            )
            self._fail(run, INDEX_FAILED_REASON)
            return None
        run.transition_task(task_id, TaskStatus.SUCCEEDED, Actor.CONTENT_DIRECTOR)
        run.record_output(
            task_id,
            payload=footage_to_payload(footage),
            schema_ref=FOOTAGE_SCHEMA_REF,
            by=Actor.CONTENT_DIRECTOR,
        )
        self._store.save(run)
        return footage

    def _render_one(
        self,
        run: Run,
        episode: IncomingEpisode,
        located: LocatedEpisode,
        clip: PlannedClip,
        outcome: _Outcome,
        *,
        task_id: str | None = None,
    ) -> None:
        """One clip: its own Task, Output, Artifact — and its own failure if it cannot be cut.

        ``task_id`` is the Task a crashed invocation left ``RUNNING`` for this clip; passing it
        continues that Task instead of opening a second one for the same clip.
        """
        if task_id is None:
            task_input = json.dumps(
                {
                    "episode_ref": episode.episode_ref,
                    "mode": episode.mode.value,
                    "index": clip.index,
                    "start_ms": clip.start_ms,
                    "end_ms": clip.end_ms,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            task_id = run.open_task(STEP_REF, AGENT_REF, task_input, Actor.CONTENT_DIRECTOR)
            run.transition_task(task_id, TaskStatus.RUNNING, Actor.CONTENT_DIRECTOR)
            self._store.save(run)  # committed before the outside call (ADR-0026 §2)

        destination = self._settings.destination_template.format(
            episode_ref=episode.episode_ref, index=clip.index
        )
        try:
            rendered = self._renderer.render(
                ClipRenderRequest(
                    located=located,
                    start_ms=clip.start_ms,
                    end_ms=clip.end_ms,
                    captions=clip.captions,
                    destination=destination,
                )
            )
        except ClipRendererError:
            self._fail_clip(run, task_id, RENDER_FAILED_REASON, outcome)
            return

        violations = check_clip_format(rendered, limits=self._settings.limits)
        if violations:
            # Not a content risk, so not a verdict for a human: the renderer produced other than
            # it was told (ADR-0056 §1).
            self._fail_clip(run, task_id, f"{FORMAT_REASON}: {violations[0]}", outcome)
            return

        payload = json.dumps(
            {
                "episode_ref": episode.episode_ref,
                "mode": episode.mode.value,
                "start_ms": clip.start_ms,
                "end_ms": clip.end_ms,
                "transcript": clip.transcript,
                "path": rendered.path,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        # The Task succeeds first: an Output belongs to a succeeded Task (ADR-0005 §5), so the
        # domain refuses the other order. All four land in one commit, so a stored Run never holds
        # a clip whose Task succeeded without its Artifact (ADR-0026 §2).
        run.transition_task(task_id, TaskStatus.SUCCEEDED, Actor.CONTENT_DIRECTOR)
        output_id = run.record_output(
            task_id, payload=payload, schema_ref=CLIP_SCHEMA_REF, by=Actor.CONTENT_DIRECTOR
        )
        artifact_id = run.create_artifact(output_id, kind=CLIP_KIND, by=Actor.CONTENT_DIRECTOR)
        run.transition_artifact(artifact_id, ArtifactStatus.CANDIDATE, Actor.CONTENT_DIRECTOR)
        self._store.save(run)

    def _fail_clip(self, run: Run, task_id: str, reason: str, outcome: _Outcome) -> None:
        """One clip's failure. The episode goes on — a clip is not the Run's fate (ADR-0059 §3)."""
        run.transition_task(task_id, TaskStatus.FAILED, Actor.CONTENT_DIRECTOR, reason=reason)
        outcome.failed_clips.append(task_id)
        self._store.save(run)

    def _fail(self, run: Run, reason: str) -> str:
        run.transition(RunStatus.FAILED, Actor.CONTENT_DIRECTOR, reason=reason)
        self._store.save(run)
        return reason

    # --- QA -----------------------------------------------------------------------------

    def _judge(self, run: Run, outcome: _Outcome) -> None:
        """Ask QA about every candidate that has no verdict yet, one Evaluation each."""
        if run.status is not RunStatus.WAITING_QA:
            return
        context = _recorded_transcript(run)
        for artifact in _candidates(run):
            if latest_qa(run, artifact.artifact_id) is not None:
                continue
            if context is None:
                # A Run from before ADR-0068 has no recorded index: nothing to judge criterion 3
                # with, so no verdict at all rather than one given blind (§5).
                outcome.qa_error = f"{NO_INDEX_ERROR}: the Run has no recorded footage index"
                return
            try:
                evaluate_artifact_in_context(run, self._evaluator, artifact.artifact_id, context)
            except Exception as exc:
                outcome.qa_error = f"{type(exc).__name__}: {exc}"
                self._store.save(run)
                return
            self._store.save(run)
        run.transition(RunStatus.WAITING_HUMAN, Actor.CONTENT_DIRECTOR)
        self._store.save(run)

    # --- the human gate -----------------------------------------------------------------

    def _review(self, run: Run, outcome: _Outcome) -> None:
        """Open, publish and read back a review per clip, then complete when all are decided."""
        if run.status is not RunStatus.WAITING_HUMAN:
            return
        self._write_posts(run, outcome)
        if self._desk is not None:
            try:
                taken = take_clip_decisions(run, self._desk)
            except Exception as exc:
                outcome.decision_error = f"{type(exc).__name__}: {exc}"
            else:
                outcome.decisions.extend(taken)
                self._record_edited_posts(run, taken)
                self._store.save(run)

        self._approve(run, outcome)
        self._publish(run, outcome)
        if open_clip_reviews(run):
            self._store.save(run)
        if self._desk is not None:
            try:
                outcome.published.extend(
                    published.location for published in publish_clip_reviews(run, self._desk)
                )
            except Exception as exc:
                outcome.publish_error = f"{type(exc).__name__}: {exc}"
        self._complete(run)

    def _write_posts(self, run: Run, outcome: _Outcome) -> None:
        """Draft the post text of every QA-passed candidate that has none yet (ADR-0072 §2).

        One ``post-text`` Task per clip, committed before the model call; one left ``RUNNING`` by
        a crash is finished, not opened again. A failed draft does not hold the review back.
        """
        if self._post_writer is None:
            return
        for artifact in _candidates(run):
            evaluation = latest_qa(run, artifact.artifact_id)
            if evaluation is None or evaluation.status is not EvaluationStatus.PASSED:
                continue
            existing = _post_tasks(run, artifact.artifact_id)
            if any(task.status is not TaskStatus.RUNNING for task in existing):
                continue
            if existing:
                task_id = existing[-1].task_id
            else:
                clip = json.loads(artifact.content)
                task_id = start_task(
                    run,
                    workflow_step_ref=POST_STEP_REF,
                    agent_ref=POST_WRITER_REF,
                    task_input=post_task_input(
                        artifact.artifact_id,
                        transcript=str(clip.get("transcript", "")),
                        episode_file=_source_ref(run),
                    ),
                )
                self._store.save(run)  # committed before the model call (ADR-0026 §2)
            try:
                finish_task(
                    run,
                    self._post_writer.executor,
                    task_id,
                    schema_binding=self._post_writer.schema_binding,
                )
            except Exception as exc:
                outcome.post_error = f"{type(exc).__name__}: {exc}"
                return
            self._store.save(run)

    def _record_edited_posts(self, run: Run, taken: Sequence[FetchedDecision]) -> None:
        """Record the reviewer's text for each recorded approval that changed it (§4)."""
        for decision in taken:
            if not decision.applied or decision.decision is not ReviewStatus.APPROVED:
                continue
            if decision.post is None:
                continue
            artifact_ref = run.human_review(decision.review_id).artifact_ref
            if decision.post == latest_post(run, artifact_ref):
                continue
            task_id = start_task(
                run,
                workflow_step_ref=POST_STEP_REF,
                agent_ref=REVIEWER_REF,
                task_input=post_task_input(artifact_ref, review_ref=decision.review_id),
            )
            run.transition_task(task_id, TaskStatus.SUCCEEDED, Actor.CONTENT_DIRECTOR)
            run.record_output(
                task_id,
                payload=json.dumps(
                    {"title": decision.post.title, "description": decision.post.description},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                schema_ref=POST_SCHEMA_REF,
                by=Actor.CONTENT_DIRECTOR,
            )

    def _publish(self, run: Run, outcome: _Outcome) -> None:
        """Post every approved clip that has no settled publication (ADR-0073 §3).

        Look before submitting: only a job the publisher has never seen is submitted, under a
        ``request_id`` fixed when the Task was opened, so no crash can post a clip twice.
        """
        if self._posting is None:
            return
        for artifact in run.artifacts:
            if artifact.status is not ArtifactStatus.APPROVED:
                continue
            task = _publish_task(run, artifact.artifact_id)
            if task is not None and task.status is not TaskStatus.RUNNING:
                continue
            post = latest_post(run, artifact.artifact_id)
            job = self._open_publication(run, artifact, post) if task is None else _job(task)
            if job is None or post is None:
                continue
            task_id, request_id, platforms = job
            try:
                status = self._look_then_submit(artifact, request_id, post, platforms)
            except ClipPublisherError as exc:
                outcome.posting_error = f"{type(exc).__name__}: {exc}"
                return
            if status.state in (PublishState.PENDING, PublishState.NOT_FOUND):
                continue
            self._settle_publication(run, artifact, task_id, request_id, post, status, outcome)
            self._store.save(run)

    def _open_publication(
        self, run: Run, artifact: ArtifactView, post: PostDraft | None
    ) -> tuple[str, str, tuple[str, ...]] | None:
        """Open the clip's ``publish-clip`` Task and commit it; ``None`` if it cannot be posted."""
        assert self._posting is not None
        request_id = f"{artifact.artifact_id}-publish"
        platforms = self._posting.platforms
        task_id = start_task(
            run,
            workflow_step_ref=PUBLISH_STEP_REF,
            agent_ref=PUBLISHER_REF,
            task_input=json.dumps(
                {
                    "artifact_ref": artifact.artifact_id,
                    "request_id": request_id,
                    "platforms": list(platforms),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        if post is None:
            # Never posted without a text a human has seen (ADR-0072, ADR-0073 §3).
            run.transition_task(
                task_id, TaskStatus.FAILED, Actor.CONTENT_DIRECTOR, reason=NO_POST_TEXT_REASON
            )
            self._store.save(run)
            return None
        self._store.save(run)  # committed before any outside call (ADR-0026 §2)
        return task_id, request_id, platforms

    def _look_then_submit(
        self,
        artifact: ArtifactView,
        request_id: str,
        post: PostDraft,
        platforms: tuple[str, ...],
    ) -> PublishStatus:
        """Ask first; submit only a job the publisher has never seen, then ask again."""
        assert self._posting is not None
        publisher = self._posting.publisher
        status = publisher.status(request_id)
        if status.state is not PublishState.NOT_FOUND:
            return status
        publisher.submit(
            PublishRequest(
                request_id=request_id,
                video_path=str(json.loads(artifact.content)["path"]),
                post=post,
                platforms=platforms,
            )
        )
        return publisher.status(request_id)

    def _settle_publication(
        self,
        run: Run,
        artifact: ArtifactView,
        task_id: str,
        request_id: str,
        post: PostDraft,
        status: PublishStatus,
        outcome: _Outcome,
    ) -> None:
        """A finished job: ``PUBLISHED`` if every platform took it, a failed Task otherwise."""
        failed = [result for result in status.results if not result.success]
        if status.state is PublishState.COMPLETED and not failed:
            run.transition_task(task_id, TaskStatus.SUCCEEDED, Actor.CONTENT_DIRECTOR)
            run.record_output(
                task_id,
                payload=json.dumps(
                    {
                        "request_id": request_id,
                        "title": post.title,
                        "results": [
                            {"platform": result.platform, "url": result.url}
                            for result in status.results
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                schema_ref=PUBLICATION_SCHEMA_REF,
                by=Actor.CONTENT_DIRECTOR,
            )
            run.transition_artifact(
                artifact.artifact_id, ArtifactStatus.PUBLISHED, Actor.CONTENT_DIRECTOR
            )
            outcome.posted.append(artifact.artifact_id)
            return
        first = failed[0] if failed else None
        detail = (
            f"{first.platform}: {first.message or 'no message'}"
            if first is not None
            else "the publisher reported a failure"
        )
        # No automatic retry: a partial post is already public somewhere (ADR-0073 §3).
        run.transition_task(
            task_id,
            TaskStatus.FAILED,
            Actor.CONTENT_DIRECTOR,
            reason=f"{PUBLISH_FAILED_REASON}: {detail}",
        )

    def _approve(self, run: Run, outcome: _Outcome) -> None:
        """Approve every candidate whose review said so and whose latest verdict passed."""
        for artifact in _candidates(run):
            approving = [
                view
                for view in run.human_reviews
                if view.artifact_ref == artifact.artifact_id
                and view.status is ReviewStatus.APPROVED
            ]
            if not approving:
                continue
            evaluation = latest_qa(run, artifact.artifact_id)
            if evaluation is None or evaluation.status is not EvaluationStatus.PASSED:
                continue
            run.transition_artifact(
                artifact.artifact_id, ArtifactStatus.APPROVED, Actor.CONTENT_DIRECTOR
            )
            outcome.approved.append(artifact.artifact_id)
            self._store.save(run)

    def _complete(self, run: Run) -> None:
        """Finish the episode once every clip has a terminal outcome, whatever those are."""
        if any(view.status is ReviewStatus.PENDING for view in run.human_reviews):
            return
        undecided = [
            artifact for artifact in _candidates(run) if _awaits_a_human(run, artifact.artifact_id)
        ]
        if undecided:
            return
        if self._posting is not None and any(
            artifact.status is ArtifactStatus.APPROVED
            and (
                (task := _publish_task(run, artifact.artifact_id)) is None
                or task.status is TaskStatus.RUNNING
            )
            for artifact in run.artifacts
        ):
            return  # an approved clip is still on its way to the platforms (ADR-0073 §3)
        run.transition(RunStatus.COMPLETED, Actor.CONTENT_DIRECTOR)
        self._store.save(run)


def _tasks_by_clip(run: Run) -> dict[int, TaskView]:
    """The Task already recorded for each clip index, read back from its stored input."""
    recorded: dict[int, TaskView] = {}
    for task in run.tasks:
        if task.workflow_step_ref != STEP_REF:
            continue
        try:
            index = json.loads(task.task_input)["index"]
        except (ValueError, KeyError, TypeError):  # pragma: no cover - our own input shape
            continue
        if isinstance(index, int):
            recorded[index] = task
    return recorded


def _job(task: TaskView) -> tuple[str, str, tuple[str, ...]]:
    """A ``RUNNING`` publish Task's id, request id and platforms, as it was opened."""
    stored = json.loads(task.task_input)
    return task.task_id, str(stored["request_id"]), tuple(str(n) for n in stored["platforms"])


def _publish_task(run: Run, artifact_id: str) -> TaskView | None:
    """The one ``publish-clip`` Task of a clip, if it was opened."""
    for task in run.tasks:
        if task.workflow_step_ref != PUBLISH_STEP_REF:
            continue
        try:
            if json.loads(task.task_input).get("artifact_ref") == artifact_id:
                return task
        except (ValueError, AttributeError):  # pragma: no cover - our own input shape
            continue
    return None


def _post_tasks(run: Run, artifact_id: str) -> list[TaskView]:
    """The model-drafted ``post-text`` Tasks of one clip, in opening order."""
    tasks: list[TaskView] = []
    for task in run.tasks:
        if task.workflow_step_ref != POST_STEP_REF or task.agent_ref != POST_WRITER_REF:
            continue
        try:
            if json.loads(task.task_input).get("artifact_ref") == artifact_id:
                tasks.append(task)
        except (ValueError, AttributeError):  # pragma: no cover - our own input shape
            continue
    return tasks


def _source_ref(run: Run) -> str:
    """The episode's file name, from the index Task's input — the one hint of the series' name."""
    task = _index_task(run)
    try:
        return str(json.loads(task.task_input)["source_ref"]) if task is not None else ""
    except (ValueError, KeyError, TypeError):  # pragma: no cover - our own input shape
        return ""


def _index_task(run: Run) -> TaskView | None:
    """The Run's index Task, if one was opened (ADR-0068 §2)."""
    return next((task for task in run.tasks if task.workflow_step_ref == INDEX_STEP_REF), None)


def _recorded_transcript(run: Run) -> str | None:
    """The whole-episode transcript QA is shown, read from the recorded index (ADR-0068 §3)."""
    task = _index_task(run)
    if task is None or task.output is None:
        return None
    try:
        return episode_transcript(footage_from_payload(task.output.payload))
    except FootageRecordError:
        return None


def _tally(run: Run) -> ClipTally:
    """Count the clips of ``run`` by where they stand (task 24.4)."""
    verdicts: dict[EvaluationStatus | None, int] = {}
    for artifact in run.artifacts:
        evaluation = latest_qa(run, artifact.artifact_id)
        status = None if evaluation is None else evaluation.status
        verdicts[status] = verdicts.get(status, 0) + 1
    return ClipTally(
        render_failed=sum(
            task.status is TaskStatus.FAILED and task.workflow_step_ref == STEP_REF
            for task in run.tasks
        ),
        passed=verdicts.get(EvaluationStatus.PASSED, 0),
        flagged=verdicts.get(EvaluationStatus.FLAGGED, 0),
        failed=verdicts.get(EvaluationStatus.FAILED, 0),
        unjudged=verdicts.get(None, 0) + verdicts.get(EvaluationStatus.PENDING, 0),
        approved=sum(
            artifact.status in (ArtifactStatus.APPROVED, ArtifactStatus.PUBLISHED)
            for artifact in run.artifacts
        ),
        posted=sum(artifact.status is ArtifactStatus.PUBLISHED for artifact in run.artifacts),
    )


def _candidates(run: Run) -> Sequence[ArtifactView]:
    return [a for a in run.artifacts if a.status is ArtifactStatus.CANDIDATE]


def _awaits_a_human(run: Run, artifact_id: str) -> bool:
    """A QA-passed candidate with no terminal review is still waiting for someone."""
    evaluation = latest_qa(run, artifact_id)
    if evaluation is None or evaluation.status is not EvaluationStatus.PASSED:
        return False  # the gate already settled it; no human decision can open it
    return not any(
        view.artifact_ref == artifact_id and view.status is not ReviewStatus.PENDING
        for view in run.human_reviews
    )
