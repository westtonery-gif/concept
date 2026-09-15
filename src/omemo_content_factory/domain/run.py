"""Run domain model — first working implementation of the aggregate.

This module implements the ``Run`` aggregate exactly as agreed in **ADR-0003 "Run domain
model interface contract"**, the sole source of the technical contract. The public interface
(the ``create`` factory, the read-only properties, ``transition`` and the
``RunStatus`` / ``Actor`` / ``ReworkPolicy`` / event / error types) is unchanged from the
accepted contract — only ``Run`` itself moves from a ``Protocol`` stub to a concrete,
instantiable aggregate, as ADR-0003 §3 requires (construction via ``Run.create()``).

**ADR-0004 "Task aggregate — Run↔Task interface contract"** additively extends Run with
management of its first child entity, ``Task`` (``open_task`` / ``transition_task`` /
read-only ``tasks`` / ``task``). This is an Open/Closed extension (PROJECT.md §4.11): Run's
existing behaviour, signatures and tests are unchanged; only new operations are added. The
``Task`` entity itself lives in ``omemo_content_factory.domain.task``.

Later ADRs add the remaining children the same additive way: Output (ADR-0005), Artifact
(ADR-0006, versioned by ADR-0019), Human Review (ADR-0007), Evaluation (ADR-0018) and Analytics
Record (ADR-0020). **ADR-0024** adds the second way a Run comes to exist (ADR-0015 §3): the
read-only ``snapshot`` observation and the ``restore`` factory, which admits a preserved state only
after verifying it (RUN_RESTORE_SPEC.md). It contains no infrastructure, persistence,
serialization, async/threading or integrations: ``RunSnapshot`` is a plain domain value, and how it
is stored is the Storage Adapter's business.

Scenario identifiers in docstrings (e.g. ``HP-01``, ``FL-03``, ``THP-01``) refer to
`RUN_ACCEPTANCE.md` and `TASK_ACCEPTANCE.md`.

Representation note (per ADR-0003 §3): identifiers and references the domain leaves opaque are
represented as plain ``str`` references — the minimal typing needed, with no added semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from omemo_content_factory.domain.analytics import (
    AnalyticsEvent,
    AnalyticsRecord,
    AnalyticsRecordCaptured,
    AnalyticsRecordId,
    Cost,
    InvalidAnalyticsRecordError,
    TimeRange,
    TokenUsage,
)
from omemo_content_factory.domain.artifact import (
    Artifact,
    ArtifactCreated,
    ArtifactEvent,
    ArtifactId,
    ArtifactNotApprovedError,
    ArtifactQaNotPassedError,
    ArtifactStatus,
    ArtifactSupersessionError,
    ArtifactView,
    DuplicateArtifactError,
)
from omemo_content_factory.domain.errors import DomainError
from omemo_content_factory.domain.evaluation import (
    Evaluation,
    EvaluationCompleted,
    EvaluationEvent,
    EvaluationId,
    EvaluationStatus,
    EvaluationView,
)
from omemo_content_factory.domain.human_review import (
    HumanReview,
    HumanReviewApproved,
    HumanReviewEvent,
    HumanReviewRejected,
    HumanReviewRequested,
    HumanReviewView,
    ReviewId,
    ReviewStatus,
)
from omemo_content_factory.domain.output import (
    Output,
    OutputEvent,
    OutputId,
    OutputStatus,
    OutputValidated,
)
from omemo_content_factory.domain.task import (
    Task,
    TaskCreated,
    TaskEvent,
    TaskId,
    TaskRetryPolicy,
    TaskSnapshot,
    TaskStatus,
    TaskView,
)


class RunStatus(Enum):
    """The seven lifecycle states of a Run (RUN_SPEC.md §4; ADR-0003 §2).

    ``COMPLETED`` and ``FAILED`` are the terminal states.
    """

    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_QA = "waiting_qa"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"


class Actor(Enum):
    """Role requesting a Run state change (ADR-0003 §4).

    Represents the existing rule "only the Content Director changes status"
    (RUN_SPEC.md §5, invariant 3). ``AGENT`` is an example of a non-authorised role.
    ``HUMAN_REVIEWER`` is the human who decides a Human Review (ADR-0007 §2); it may submit a
    review decision but not drive Run/Task/Output/Artifact state.
    """

    CONTENT_DIRECTOR = "content_director"
    AGENT = "agent"
    HUMAN_REVIEWER = "human_reviewer"


@dataclass(frozen=True, slots=True)
class ReworkPolicy:
    """Value Object bounding the number of rework iterations (ADR-0003 §6).

    ``max_rework_iterations`` is a configurable policy value, not a domain rule; the default
    is ``3``. Governs scenarios ``RW-04`` / ``INV-08``.
    """

    max_rework_iterations: int = 3


# --- Domain events -----------------------------------------------------------------------
# Emitted by a Run on successful transitions (ADR-0003 §7). Verified by EV-01..EV-05.


@dataclass(frozen=True, slots=True)
class RunEvent:
    """Base type for Run domain events. Carries the owning Run identifier."""

    run_id: str


@dataclass(frozen=True, slots=True)
class RunCreated(RunEvent):
    """Emitted when a Run is created (`EV-01`; happy path `HP-01`).

    Carries the fixed input references captured at creation (`ID-02`).
    """

    content_brief_ref: str
    workflow_version_ref: str


@dataclass(frozen=True, slots=True)
class RunQueued(RunEvent):
    """Emitted on ``CREATED -> QUEUED`` (`EV-02`)."""


@dataclass(frozen=True, slots=True)
class RunStarted(RunEvent):
    """Emitted on the first ``QUEUED -> RUNNING`` only (`EV-03`).

    Re-entry to ``RUNNING`` via rework does **not** emit this event (RUN_SPEC.md §7).
    """


@dataclass(frozen=True, slots=True)
class RunCompleted(RunEvent):
    """Emitted on reaching ``COMPLETED`` after Approve (`EV-04`; `HP-02`)."""


@dataclass(frozen=True, slots=True)
class RunFailed(RunEvent):
    """Emitted on reaching ``FAILED`` (`EV-05`; `FL-12`). Carries the failure reason."""

    reason: str | None = None


RunLogEvent: TypeAlias = (
    RunEvent
    | TaskEvent
    | OutputEvent
    | ArtifactEvent
    | HumanReviewEvent
    | EvaluationEvent
    | AnalyticsEvent
)
"""Any event recorded in a Run's single log: the Run's own and those of its child entities."""


# --- Domain errors -----------------------------------------------------------------------
# Raised on domain-rule violations (ADR-0003 §8). Distinct from technical failures. Rooted at the
# shared ``DomainError`` (ADR-0017).


class RunDomainError(DomainError):
    """Base class for all Run domain-rule violations."""


class InvalidTransitionError(RunDomainError):
    """A requested state transition is not allowed.

    Covers forbidden edges, reopening a terminal state and state skips
    (`FL-01`, `FL-03`, `FL-04`, `FL-09`, `FL-10`, `FL-11`; invariants `INV-04`, `INV-05`).
    """


class UnauthorizedActorError(RunDomainError):
    """A state change was requested by an actor other than the Content Director.

    Covers `FL-08` (invariant `INV-03`).
    """


class ImmutableAttributeError(RunDomainError):
    """An attempt was made to change an immutable attribute of a Run.

    Covers the run identifier and fixed input references
    (`FL-05`, `FL-06`, `FL-07`; invariant `INV-02`; identity `ID-01`, `ID-02`).
    """


class ReworkLimitExceededError(RunDomainError):
    """A rework re-entry would exceed the configured rework policy bound.

    Covers `RW-04` (invariant `INV-08`).
    """


class AggregateBoundaryError(RunDomainError):
    """A child was created or attached outside its owning Run, or bound to another Run.

    Covers `AGG-01`..`AGG-06` (invariant `INV-07`). Child entity types are out of scope at
    this stage (ADR-0003 §9), so this error exists in the contract ahead of those entities.
    """


class RunRestorationError(RunDomainError):
    """A snapshot does not describe a Run that could legally exist, so it is not restored.

    Raised by ``Run.restore`` when the preserved state breaks an invariant of RUN_SPEC.md §5. No
    Run is returned, not even a partial one (RUN_SPEC.md §4a verify/reject; RUN_RESTORE_SPEC §4.3).
    """


# --- Snapshot (additive, ADR-0024) --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """The complete observable truth of one Run at a point in time (RUN_RESTORE_SPEC §3).

    Taken with :attr:`Run.snapshot` and turned back into the same Run by :meth:`Run.restore`. A
    plain, persistence-ignorant domain value with no behaviour (RUN_SPEC.md §9). Beyond what the
    read-only views show, it carries the two policies and the five id counters. Without them a
    restored Run would reset its bounds or hand out an id that is already taken (ADR-0015 I3).
    Children appear as their complete views (``TaskSnapshot`` adds the retry policy). The journal
    is taken verbatim.
    """

    run_id: str
    content_brief_ref: str
    workflow_version_ref: str
    status: RunStatus
    rework_count: int
    failure_reason: str | None
    rework_policy: ReworkPolicy
    task_seq: int
    artifact_seq: int
    review_seq: int
    evaluation_seq: int
    analytics_seq: int
    tasks: tuple[TaskSnapshot, ...]
    artifacts: tuple[ArtifactView, ...]
    human_reviews: tuple[HumanReviewView, ...]
    evaluations: tuple[EvaluationView, ...]
    analytics_records: tuple[AnalyticsRecord, ...]
    events: tuple[RunLogEvent, ...]


# --- Run aggregate -----------------------------------------------------------------------

# Allowed state transitions (RUN_SPEC.md §4). A terminal state has no outgoing edges, so any
# transition out of it is rejected. "Any non-terminal -> FAILED" is encoded per source.
_ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.CREATED: {RunStatus.QUEUED, RunStatus.FAILED},
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.FAILED},
    RunStatus.RUNNING: {RunStatus.WAITING_QA, RunStatus.FAILED},
    RunStatus.WAITING_QA: {RunStatus.WAITING_HUMAN, RunStatus.RUNNING, RunStatus.FAILED},
    RunStatus.WAITING_HUMAN: {RunStatus.COMPLETED, RunStatus.RUNNING, RunStatus.FAILED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
}

# States from which a return to RUNNING counts as a rework iteration (RUN_SPEC.md §4, §7).
_REWORK_SOURCES: set[RunStatus] = {RunStatus.WAITING_QA, RunStatus.WAITING_HUMAN}

# Immutable public attributes AND their private backing fields. Neither a public name
# (``run.run_id = ...``) nor its backing field (``run._run_id = ...``) may be reassigned
# after construction, so immutability is real rather than surface-only (ADR-0003 §3, §8).
_IMMUTABLE_ATTRIBUTES = frozenset(
    {
        "run_id",
        "content_brief_ref",
        "workflow_version_ref",
        "_run_id",
        "_content_brief_ref",
        "_workflow_version_ref",
    }
)


class Run:
    """The Run aggregate root — the consistency boundary of one content production.

    Concrete implementation of the contract in ADR-0003. Construct via :meth:`create`.
    The Content Director is the only actor permitted to drive state transitions.

    ``__slots__`` (no instance ``__dict__``) together with a guarded ``__setattr__`` make
    the immutable input fields genuinely write-once: both the public names and their
    private backing fields are protected, and stray attribute injection is closed off.
    """

    __slots__ = (
        "_analytics_records",
        "_analytics_seq",
        "_artifact_seq",
        "_artifacts",
        "_content_brief_ref",
        "_evaluation_seq",
        "_evaluations",
        "_events",
        "_failure_reason",
        "_human_reviews",
        "_review_seq",
        "_rework_count",
        "_rework_policy",
        "_run_id",
        "_status",
        "_task_seq",
        "_tasks",
        "_workflow_version_ref",
    )

    _run_id: str
    _content_brief_ref: str
    _workflow_version_ref: str
    _rework_policy: ReworkPolicy
    _status: RunStatus
    _rework_count: int
    _failure_reason: str | None
    _events: list[RunLogEvent]
    _tasks: dict[TaskId, Task]
    _task_seq: int
    _artifacts: dict[ArtifactId, Artifact]
    _artifact_seq: int
    _human_reviews: dict[ReviewId, HumanReview]
    _review_seq: int
    _evaluations: dict[EvaluationId, Evaluation]
    _evaluation_seq: int
    _analytics_records: dict[AnalyticsRecordId, AnalyticsRecord]
    _analytics_seq: int

    def __init__(
        self,
        run_id: str,
        content_brief_ref: str,
        workflow_version_ref: str,
        rework_policy: ReworkPolicy,
    ) -> None:
        # Immutable inputs are written exactly once, here, bypassing the guard below.
        object.__setattr__(self, "_run_id", run_id)
        object.__setattr__(self, "_content_brief_ref", content_brief_ref)
        object.__setattr__(self, "_workflow_version_ref", workflow_version_ref)
        # Mutable state is assigned normally and flows through the guarded __setattr__.
        self._rework_policy = rework_policy
        self._status = RunStatus.CREATED
        self._rework_count = 0
        self._failure_reason = None
        self._events = []
        self._tasks = {}
        self._task_seq = 0
        self._artifacts = {}
        self._artifact_seq = 0
        self._human_reviews = {}
        self._review_seq = 0
        self._evaluations = {}
        self._evaluation_seq = 0
        self._analytics_records = {}
        self._analytics_seq = 0

    def __setattr__(self, name: str, value: object) -> None:
        """Reject reassignment of immutable attributes and their backing fields.

        Guards both the public names (``run.run_id = ...``) and the private storage
        (``run._run_id = ...``), so the protection is real, not surface-only
        (`FL-05`, `FL-06`, `FL-07`; `INV-02`).
        """
        if name in _IMMUTABLE_ATTRIBUTES:
            raise ImmutableAttributeError(f"'{name}' is immutable after creation")
        super().__setattr__(name, value)

    @classmethod
    def create(
        cls,
        run_id: str,
        content_brief_ref: str,
        workflow_version_ref: str,
        rework_policy: ReworkPolicy | None = None,
    ) -> Run:
        """Create a Run in ``CREATED`` and emit ``RunCreated``.

        ``run_id``, ``content_brief_ref`` and ``workflow_version_ref`` are explicit and become
        read-only (`ID-01`, `ID-02`; `INV-02`). ``rework_policy`` defaults to ``ReworkPolicy()``
        when ``None``. Establishes the start of the happy path (`HP-01`, `EV-01`).
        """
        run = cls(
            run_id=run_id,
            content_brief_ref=content_brief_ref,
            workflow_version_ref=workflow_version_ref,
            rework_policy=rework_policy if rework_policy is not None else ReworkPolicy(),
        )
        run._record(
            RunCreated(
                run_id=run_id,
                content_brief_ref=content_brief_ref,
                workflow_version_ref=workflow_version_ref,
            )
        )
        return run

    @classmethod
    def restore(cls, snapshot: RunSnapshot) -> Run:
        """Bring a Run back at its preserved state, the second way a Run comes to exist.

        Additive to :meth:`create` (ADR-0015 §3; RUN_RESTORE_SPEC §4). No transition is made and no
        event is emitted: the original Run reached this state legally, and the journal is taken
        verbatim, so nothing is replayed or recorded twice (ADR-0015 I2, I3). Legality is checked,
        not assumed. A snapshot that breaks an invariant of RUN_SPEC.md §5 raises
        ``RunRestorationError`` and no Run is returned (RUN_SPEC.md §4a). From here the Run advances
        under the unchanged transition contract, and its id counters continue where they stopped.
        """
        _verify_snapshot(snapshot)
        run = cls(
            run_id=snapshot.run_id,
            content_brief_ref=snapshot.content_brief_ref,
            workflow_version_ref=snapshot.workflow_version_ref,
            rework_policy=snapshot.rework_policy,
        )
        run._status = snapshot.status
        run._rework_count = snapshot.rework_count
        run._failure_reason = snapshot.failure_reason
        run._events = list(snapshot.events)
        run._tasks = {task.task_id: Task.restore(task) for task in snapshot.tasks}
        run._task_seq = snapshot.task_seq
        run._artifacts = {view.artifact_id: Artifact.restore(view) for view in snapshot.artifacts}
        run._artifact_seq = snapshot.artifact_seq
        run._human_reviews = {
            view.review_id: HumanReview.restore(view) for view in snapshot.human_reviews
        }
        run._review_seq = snapshot.review_seq
        run._evaluations = {
            view.evaluation_id: Evaluation.restore(view) for view in snapshot.evaluations
        }
        run._evaluation_seq = snapshot.evaluation_seq
        run._analytics_records = {record.record_id: record for record in snapshot.analytics_records}
        run._analytics_seq = snapshot.analytics_seq
        if run._status is RunStatus.COMPLETED and not all(
            task.is_terminal for task in run._tasks.values()
        ):
            raise RunRestorationError("a COMPLETED Run cannot own a non-terminal Task")
        return run

    @property
    def snapshot(self) -> RunSnapshot:
        """The complete observable truth of this Run, at this point in time (ADR-0024).

        A read-only observation, like the views: taking it changes nothing, and it does not follow
        later changes. ``Run.restore(run.snapshot)`` gives back a Run indistinguishable from this
        one (RUN_RESTORE_SPEC §3.2, §4.4).
        """
        return RunSnapshot(
            run_id=self._run_id,
            content_brief_ref=self._content_brief_ref,
            workflow_version_ref=self._workflow_version_ref,
            status=self._status,
            rework_count=self._rework_count,
            failure_reason=self._failure_reason,
            rework_policy=self._rework_policy,
            task_seq=self._task_seq,
            artifact_seq=self._artifact_seq,
            review_seq=self._review_seq,
            evaluation_seq=self._evaluation_seq,
            analytics_seq=self._analytics_seq,
            tasks=tuple(task.snapshot for task in self._tasks.values()),
            artifacts=tuple(self.artifacts),
            human_reviews=tuple(self.human_reviews),
            evaluations=tuple(self.evaluations),
            analytics_records=tuple(self.analytics_records),
            events=tuple(self._events),
        )

    @property
    def run_id(self) -> str:
        """Stable, immutable identity of the Run (`ID-01`; `FL-07`)."""
        return self._run_id

    @property
    def content_brief_ref(self) -> str:
        """Fixed reference to the Content Brief; immutable after creation (`FL-05`, `ID-02`)."""
        return self._content_brief_ref

    @property
    def workflow_version_ref(self) -> str:
        """Fixed reference to the Workflow version; immutable after creation (`FL-06`)."""
        return self._workflow_version_ref

    @property
    def status(self) -> RunStatus:
        """The single current state of the Run — exactly one at any time (`INV-01`)."""
        return self._status

    @property
    def rework_count(self) -> int:
        """Number of rework iterations performed so far (`RW-04`; `INV-08`)."""
        return self._rework_count

    @property
    def failure_reason(self) -> str | None:
        """Reason captured when the Run reached ``FAILED`` (`FL-12`; `EV-05`)."""
        return self._failure_reason

    @property
    def events(self) -> Sequence[RunLogEvent]:
        """Ordered, read-only sequence of events emitted since creation.

        Holds Run events (`EV-01`..`EV-05`), Task events (`TEV-01`..`TEV-06`, ADR-0004 §7),
        Output events (`OutputValidated`, ADR-0005 §7), Artifact events (`ArtifactCreated`,
        ADR-0006 §8), Human Review events (ADR-0007 §7), Evaluation events
        (`EvaluationCompleted`, ADR-0018 §8) and Analytics Record events
        (`AnalyticsRecordCaptured`, ADR-0020 §6), in one Run log.
        """
        return tuple(self._events)

    def transition(self, to: RunStatus, by: Actor, reason: str | None = None) -> None:
        """Request a guarded state transition driven by ``by`` (the Content Director).

        Permits only the edges of RUN_SPEC.md §4 and emits the corresponding event (ADR-0003
        §5, §7). Drives the happy path and rework (`HP-01`, `HP-02`, `RW-01`..`RW-03`) and
        rejects domain-rule violations via the errors above (`FL-01`..`FL-12`; invariants
        `INV-01`..`INV-06`, `INV-08`; events `EV-02`..`EV-05`). ``reason`` accompanies a
        ``FAILED`` transition.
        """
        self._ensure_authorised(by)
        self._ensure_allowed(to)
        self._ensure_children_terminal_for_completion(to)
        if self._is_rework(to):
            self._register_rework()
        previous = self._status
        self._status = to
        if to is RunStatus.FAILED:
            self._failure_reason = reason
        event = self._event_for(previous, to, reason)
        if event is not None:
            self._record(event)

    # --- Task child management (additive, ADR-0004) --------------------------------------

    def open_task(
        self,
        workflow_step_ref: str,
        agent_ref: str,
        task_input: str,
        by: Actor,
        retry_policy: TaskRetryPolicy | None = None,
    ) -> TaskId:
        """Create a child Task in ``PENDING`` inside this Run and emit ``TaskCreated``.

        Authorised actor only (the Content Director); the Task is owned by this Run, its id is
        generated deterministically and returned (ADR-0004 §3, §4). ``retry_policy`` defaults
        to ``TaskRetryPolicy()`` when ``None`` (`THP-01`, `TAGG-01`, `INV-07`; event `TEV-01`).
        """
        self._ensure_authorised(by)
        self._task_seq += 1
        task_id = f"{self._run_id}-task-{self._task_seq}"
        self._tasks[task_id] = Task(
            task_id=task_id,
            run_id=self._run_id,
            workflow_step_ref=workflow_step_ref,
            agent_ref=agent_ref,
            task_input=task_input,
            retry_policy=retry_policy if retry_policy is not None else TaskRetryPolicy(),
        )
        self._record(
            TaskCreated(
                run_id=self._run_id,
                task_id=task_id,
                workflow_step_ref=workflow_step_ref,
                agent_ref=agent_ref,
            )
        )
        return task_id

    def transition_task(
        self, task_id: TaskId, to: TaskStatus, by: Actor, reason: str | None = None
    ) -> None:
        """Drive a guarded transition of an owned Task through the root (ADR-0004 §4, §5).

        Actor authorisation (Content Director only) plus the Task allowed-transitions table and
        the attempt bound make up the single mutation; any emitted Task event is recorded in
        this Run's event log (`THP-02`..`THP-04`, `TRW-*`, `TFL-01`..`TFL-03`, `TEV-*`).
        """
        self._ensure_authorised(by)
        event = self._tasks[task_id].apply_transition(to, reason)
        if event is not None:
            self._record(event)

    def record_output(
        self, task_id: TaskId, *, payload: str, schema_ref: str, by: Actor, valid: bool = True
    ) -> OutputId:
        """Persist the immutable Output of a succeeded Task, through the root (ADR-0005 §5).

        **Pure execution sink** (evaluation-ownership model = Variant A): Run only *persists* the
        given verdict — it does **not** import Schema and does **not** decide validity. The
        VALID/INVALID decision is owned by the Schema authority (``Schema.validate``), invoked in
        the application layer, which passes the outcome here as ``valid``.

        Authorised actor only (the Content Director). The Task must be ``SUCCEEDED`` and must not
        already have an Output (1:1) — both enforced by the Task. Records the Output ``VALID`` when
        ``valid`` (default — legacy behaviour) else ``INVALID``; emits ``OutputValidated`` only for
        a valid result (an INVALID outcome emits no dedicated event, ADR-0008 §9). Returns the new
        Output's id.
        """
        self._ensure_authorised(by)
        task = self._tasks[task_id]
        output_id = f"{task_id}-output"
        output = Output(
            output_id=output_id,
            task_id=task_id,
            schema_ref=schema_ref,
            payload=payload,
            status=OutputStatus.VALID if valid else OutputStatus.INVALID,
        )
        task.attach_output(output)
        if valid:
            self._record(OutputValidated(run_id=self._run_id, task_id=task_id, output_id=output_id))
        return output_id

    @property
    def tasks(self) -> Sequence[TaskView]:
        """Read-only snapshots of the Tasks owned by this Run (ADR-0004 §4; `TAGG-03`)."""
        return tuple(child.view for child in self._tasks.values())

    def task(self, task_id: TaskId) -> TaskView:
        """Read-only snapshot of one owned Task (ADR-0004 §4; `TAGG-03`)."""
        return self._tasks[task_id].view

    # --- Artifact child management (additive, ADR-0006) ----------------------------------

    def create_artifact(self, output_id: OutputId, *, kind: str, by: Actor) -> ArtifactId:
        """Create a ``DRAFT`` Artifact from an existing Output, through the root (ADR-0006 §5, §6).

        Authorised actor only (the Content Director). The ``output_id`` must reference an Output
        recorded in this Run (else ``KeyError``), and that Output must not already have produced
        an Artifact (1:1, else ``DuplicateArtifactError``). The new Artifact takes its content
        from the Output's payload and records the provenance; ``ArtifactCreated`` is emitted.
        """
        self._ensure_authorised(by)
        output = self._require_output(output_id)
        self._ensure_output_unused(output_id)
        return self._add_artifact(output, kind=kind, version=1, supersedes_ref=None)

    def create_artifact_version(
        self,
        previous_artifact_id: ArtifactId,
        output_id: OutputId,
        *,
        by: Actor,
        kind: str | None = None,
    ) -> ArtifactId:
        """Create the next version of an Artifact from a reworked Output (ADR-0019 §4).

        The rework path: a fixed Artifact version is immutable, so addressing a QA risk verdict or a
        human ``Request changes`` means producing a **new** Artifact — ``version`` + 1, its own
        Output provenance (1:1 is preserved), ``supersedes_ref`` pointing at the predecessor — while
        that predecessor becomes ``SUPERSEDED`` in the same operation (DOMAIN_MODEL.md §2.12, §6,
        §9.1). ``kind`` defaults to the predecessor's when ``None``.

        Authorised actor only (the Content Director). The predecessor must be owned by this Run
        (else ``KeyError``) and still in a **working** state — a ``PUBLISHED``, ``REJECTED`` or
        already ``SUPERSEDED`` version is terminal and cannot be superseded
        (``InvalidArtifactTransitionError``), which also keeps the version chain linear. The Output
        must be recorded in this Run (``KeyError``) and unused (``DuplicateArtifactError``); all
        checks run **before** any state changes, so a rejected call supersedes nothing.

        The successor is a new Artifact with a new id, so it starts its own review/QA lifecycle from
        scratch: the predecessor's Human Review and Evaluation target the old id and never carry
        over (ADR-0019 §6). Emits ``ArtifactCreated`` with the new version and ``supersedes_ref``.
        """
        self._ensure_authorised(by)
        previous = self._artifacts[previous_artifact_id]
        previous_view = previous.view
        output = self._require_output(output_id)
        self._ensure_output_unused(output_id)
        # Everything that can fail has been checked: supersede, then create, so the Run never ends
        # up with a superseded version and no successor.
        previous.apply_transition(ArtifactStatus.SUPERSEDED)
        return self._add_artifact(
            output,
            kind=previous_view.kind if kind is None else kind,
            version=previous_view.version + 1,
            supersedes_ref=previous_artifact_id,
        )

    def _add_artifact(
        self, output: Output, *, kind: str, version: int, supersedes_ref: str | None
    ) -> ArtifactId:
        """Register a new Artifact over ``output`` and emit ``ArtifactCreated`` (ADR-0006 §5)."""
        self._artifact_seq += 1
        artifact_id = f"{self._run_id}-artifact-{self._artifact_seq}"
        self._artifacts[artifact_id] = Artifact(
            artifact_id=artifact_id,
            run_id=self._run_id,
            output_ref=output.output_id,
            kind=kind,
            content=output.payload,
            version=version,
            supersedes_ref=supersedes_ref,
        )
        self._record(
            ArtifactCreated(
                run_id=self._run_id,
                artifact_id=artifact_id,
                output_ref=output.output_id,
                kind=kind,
                version=version,
                supersedes_ref=supersedes_ref,
            )
        )
        return artifact_id

    def transition_artifact(self, artifact_id: ArtifactId, to: ArtifactStatus, by: Actor) -> None:
        """Drive a guarded status transition of an owned Artifact through the root.

        Authorised actor only (the Content Director). Edges: ``DRAFT -> CANDIDATE`` (ADR-0006);
        ``CANDIDATE -> APPROVED`` / ``REJECTED`` and ``APPROVED -> PUBLISHED`` (ADR-0007 §6).
        ``CANDIDATE -> APPROVED`` additionally requires an approving Human Review for this
        Artifact, else ``ArtifactNotApprovedError`` — so ``PUBLISHED`` is unreachable without an
        ``Approve`` (DOMAIN_MODEL.md §6). It also requires the latest QA Evaluation of this
        Artifact to be ``PASSED``, else ``ArtifactQaNotPassedError`` — fail closed: no, a pending
        or a risk verdict keeps the gate shut, even after an ``Approve`` (ADR-0018 §5).
        ``SUPERSEDED`` is **not** reachable here: it is one half of creating the next version, so it
        is taken only by ``create_artifact_version``, else ``ArtifactSupersessionError``
        (ADR-0019 §4).
        """
        self._ensure_authorised(by)
        artifact = self._artifacts[artifact_id]
        if to is ArtifactStatus.SUPERSEDED:
            raise ArtifactSupersessionError(
                f"artifact {artifact_id} is superseded only by creating its next version "
                "(Run.create_artifact_version)"
            )
        approving = (
            to is ArtifactStatus.APPROVED and artifact.view.status is ArtifactStatus.CANDIDATE
        )
        if approving and not self._has_approved_review(artifact_id):
            raise ArtifactNotApprovedError(
                f"artifact {artifact_id} cannot be APPROVED without an approving Human Review"
            )
        if approving and not self._qa_passed(artifact_id):
            raise ArtifactQaNotPassedError(
                f"artifact {artifact_id} cannot be APPROVED without a PASSED latest QA Evaluation"
            )
        artifact.apply_transition(to)

    @property
    def artifacts(self) -> Sequence[ArtifactView]:
        """Read-only snapshots of the Artifacts owned by this Run (ADR-0006 §6)."""
        return tuple(artifact.view for artifact in self._artifacts.values())

    def artifact(self, artifact_id: ArtifactId) -> ArtifactView:
        """Read-only snapshot of one owned Artifact (ADR-0006 §6)."""
        return self._artifacts[artifact_id].view

    def _require_output(self, output_id: OutputId) -> Output:
        """Return the owned Output with ``output_id``, scanning Tasks; ``KeyError`` if unknown."""
        for task in self._tasks.values():
            output = task.output
            if output is not None and output.output_id == output_id:
                return output
        raise KeyError(output_id)

    def _ensure_output_unused(self, output_id: OutputId) -> None:
        """Enforce Output->Artifact 1:1 (ADR-0006 §5)."""
        if any(artifact.output_ref == output_id for artifact in self._artifacts.values()):
            raise DuplicateArtifactError(f"output {output_id} already has an Artifact")

    # --- Human Review child management (additive, ADR-0007) ------------------------------

    def open_human_review(self, artifact_id: ArtifactId, *, by: Actor) -> ReviewId:
        """Open a ``PENDING`` Human Review on a candidate Artifact, through the root (ADR-0007 §5).

        Content Director only. The target Artifact must be ``CANDIDATE`` (else
        ``InvalidArtifactTransitionError``). Emits ``HumanReviewRequested`` and returns the id.
        """
        self._ensure_authorised(by)
        status = self._artifacts[artifact_id].view.status
        if status is not ArtifactStatus.CANDIDATE:
            raise InvalidTransitionError(
                f"a Human Review needs a CANDIDATE Artifact; {artifact_id} is {status}"
            )
        self._review_seq += 1
        review_id = f"{self._run_id}-review-{self._review_seq}"
        self._human_reviews[review_id] = HumanReview(
            review_id=review_id, run_id=self._run_id, artifact_ref=artifact_id
        )
        self._record(
            HumanReviewRequested(run_id=self._run_id, review_id=review_id, artifact_ref=artifact_id)
        )
        return review_id

    def submit_review(
        self, review_id: ReviewId, decision: ReviewStatus, *, by: Actor, reason: str | None = None
    ) -> None:
        """Record the human's decision on a review, through the root (ADR-0007 §5).

        Human Reviewer only (not the Content Director). The review must be ``PENDING``. Emits
        ``HumanReviewApproved`` / ``HumanReviewRejected`` (``CHANGES_REQUESTED`` emits no event).
        """
        self._ensure_reviewer(by)
        review = self._human_reviews[review_id]
        review.decide(decision, decided_by=by.value, reason=reason)
        if decision is ReviewStatus.APPROVED:
            self._record(
                HumanReviewApproved(run_id=self._run_id, review_id=review_id, decided_by=by.value)
            )
        elif decision is ReviewStatus.REJECTED:
            self._record(
                HumanReviewRejected(
                    run_id=self._run_id, review_id=review_id, decided_by=by.value, reason=reason
                )
            )

    @property
    def human_reviews(self) -> Sequence[HumanReviewView]:
        """Read-only snapshots of the Human Reviews owned by this Run (ADR-0007 §5)."""
        return tuple(review.view for review in self._human_reviews.values())

    def human_review(self, review_id: ReviewId) -> HumanReviewView:
        """Read-only snapshot of one owned Human Review (ADR-0007 §5)."""
        return self._human_reviews[review_id].view

    # --- Evaluation child management (additive, ADR-0018) --------------------------------

    def open_evaluation(self, artifact_id: ArtifactId, *, kind: str, by: Actor) -> EvaluationId:
        """Open a ``PENDING`` Evaluation of a candidate Artifact, through the root (ADR-0018 §4).

        Content Director only. The target Artifact must be ``CANDIDATE`` (else
        ``InvalidTransitionError``). Emits no event (only the verdict is an event,
        DOMAIN_MODEL.md §10). A re-evaluation is a new Evaluation. Returns the new id.
        """
        self._ensure_authorised(by)
        status = self._artifacts[artifact_id].view.status
        if status is not ArtifactStatus.CANDIDATE:
            raise InvalidTransitionError(
                f"an Evaluation needs a CANDIDATE Artifact; {artifact_id} is {status}"
            )
        self._evaluation_seq += 1
        evaluation_id = f"{self._run_id}-evaluation-{self._evaluation_seq}"
        self._evaluations[evaluation_id] = Evaluation(
            evaluation_id=evaluation_id, run_id=self._run_id, artifact_ref=artifact_id, kind=kind
        )
        return evaluation_id

    def record_evaluation(
        self,
        evaluation_id: EvaluationId,
        verdict: EvaluationStatus,
        *,
        by: Actor,
        flags: tuple[str, ...] = (),
    ) -> None:
        """Persist the verdict of an Evaluation, through the root (ADR-0018 §3, §4).

        **Pure sink** (Variant A, ADR-0013 §8): the verdict is decided by the QA evaluator outside
        the domain; Run only records it. Content Director only. The evaluation must be ``PENDING``
        and ``verdict`` terminal (else ``InvalidEvaluationTransitionError``). Emits
        ``EvaluationCompleted``.
        """
        self._ensure_authorised(by)
        evaluation = self._evaluations[evaluation_id]
        evaluation.decide(verdict, flags)
        self._record(
            EvaluationCompleted(
                run_id=self._run_id,
                evaluation_id=evaluation_id,
                artifact_ref=evaluation.artifact_ref,
                verdict=verdict,
                flags=flags,
            )
        )

    @property
    def evaluations(self) -> Sequence[EvaluationView]:
        """Read-only snapshots of the Evaluations owned by this Run, in opening order."""
        return tuple(evaluation.view for evaluation in self._evaluations.values())

    def evaluation(self, evaluation_id: EvaluationId) -> EvaluationView:
        """Read-only snapshot of one owned Evaluation (ADR-0018 §4)."""
        return self._evaluations[evaluation_id].view

    def _qa_passed(self, artifact_id: ArtifactId) -> bool:
        """Whether the latest Evaluation of ``artifact_id`` is ``PASSED`` (ADR-0018 §5).

        Evaluations are kept in opening order, so the last match is the latest. No evaluation is
        not a pass (fail closed).
        """
        latest: Evaluation | None = None
        for evaluation in self._evaluations.values():
            if evaluation.artifact_ref == artifact_id:
                latest = evaluation
        return latest is not None and latest.status is EvaluationStatus.PASSED

    def _has_approved_review(self, artifact_id: ArtifactId) -> bool:
        """Whether an ``APPROVED`` Human Review targets ``artifact_id`` (ADR-0007 §6)."""
        return any(
            review.artifact_ref == artifact_id and review.status is ReviewStatus.APPROVED
            for review in self._human_reviews.values()
        )

    # --- Analytics Record child management (additive, ADR-0020) --------------------------

    def record_analytics(
        self,
        task_id: TaskId,
        *,
        provider: str,
        model: str,
        token_usage: TokenUsage,
        cost: Cost,
        time_range: TimeRange,
        by: Actor,
        prompt_ref: str | None = None,
    ) -> AnalyticsRecordId:
        """Record the metrics of one agent call made for an owned Task (ADR-0020 §5).

        Append-only (DOMAIN_MODEL.md §2.15): the record is immutable and there is no update or
        delete. Content Director only. The Task must be owned by this Run (else ``KeyError``) and
        must have been started — a Task that never entered ``RUNNING`` made no call (else
        ``InvalidAnalyticsRecordError``). Nothing else about the Run's or the Task's state is
        checked: a failed call, or one recorded after the Task or the Run ended, still happened and
        cost money (PROJECT.md §16).

        Attribution is derived, never asserted (ADR-0020 §4): ``run_id``, ``task_id`` and
        ``agent_ref`` come from the Task, and ``retries`` is its ``attempt_count - 1`` at capture.
        The record is validated before anything changes, so a refused call records nothing and
        consumes no id. Emits ``AnalyticsRecordCaptured``.
        """
        self._ensure_authorised(by)
        task = self._tasks[task_id].view
        if task.attempt_count < 1:
            raise InvalidAnalyticsRecordError(
                f"task {task_id} was never started, so it made no call to record"
            )
        record_id = f"{self._run_id}-analytics-{self._analytics_seq + 1}"
        record = AnalyticsRecord(
            record_id=record_id,
            run_id=self._run_id,
            task_id=task_id,
            agent_ref=task.agent_ref,
            provider=provider,
            model=model,
            token_usage=token_usage,
            cost=cost,
            time_range=time_range,
            retries=task.attempt_count - 1,
            prompt_ref=prompt_ref,
        )
        self._analytics_seq += 1
        self._analytics_records[record_id] = record
        self._record(
            AnalyticsRecordCaptured(
                run_id=self._run_id, record_id=record_id, task_id=task_id, agent_ref=task.agent_ref
            )
        )
        return record_id

    @property
    def analytics_records(self) -> Sequence[AnalyticsRecord]:
        """The Analytics Records owned by this Run, in capture order (immutable, ADR-0020 §5)."""
        return tuple(self._analytics_records.values())

    def analytics_record(self, record_id: AnalyticsRecordId) -> AnalyticsRecord:
        """One owned Analytics Record (immutable, ADR-0020 §5)."""
        return self._analytics_records[record_id]

    def _ensure_reviewer(self, by: Actor) -> None:
        """Only the Human Reviewer may submit a review decision (ADR-0007 §2)."""
        if by is not Actor.HUMAN_REVIEWER:
            raise UnauthorizedActorError(f"{by} may not submit a Human Review decision")

    def _ensure_authorised(self, by: Actor) -> None:
        """Only the Content Director may change status (`FL-08`; `INV-03`)."""
        if by is not Actor.CONTENT_DIRECTOR:
            raise UnauthorizedActorError(f"{by} may not change Run status")

    def _ensure_allowed(self, to: RunStatus) -> None:
        """Reject any edge outside the allowed table, including out of a terminal (`FL-*`)."""
        if to not in _ALLOWED_TRANSITIONS[self._status]:
            raise InvalidTransitionError(f"transition {self._status} -> {to} is not allowed")

    def _ensure_children_terminal_for_completion(self, to: RunStatus) -> None:
        """Forbid reaching COMPLETED while any owned Task is non-terminal (ADR-0004 §9).

        Cross-entity invariant: production is not successfully done while a step is unfinished
        (`TXC-01`/`TXC-02`). Vacuously satisfied for a Run with no Tasks, so the Run reference
        behaviour is unchanged.
        """
        if to is not RunStatus.COMPLETED:
            return
        if any(not child.is_terminal for child in self._tasks.values()):
            raise InvalidTransitionError("cannot reach COMPLETED while a Task is non-terminal")

    def _is_rework(self, to: RunStatus) -> bool:
        """A return to RUNNING from a waiting state is a rework iteration (RUN_SPEC.md §4)."""
        return to is RunStatus.RUNNING and self._status in _REWORK_SOURCES

    def _register_rework(self) -> None:
        """Bound rework by the policy; reject the iteration that would exceed it (`RW-04`)."""
        if self._rework_count >= self._rework_policy.max_rework_iterations:
            raise ReworkLimitExceededError("rework limit exceeded")
        self._rework_count += 1

    def _event_for(self, previous: RunStatus, to: RunStatus, reason: str | None) -> RunEvent | None:
        """Map a successful edge to its Run event, or ``None`` when the edge emits none."""
        if to is RunStatus.QUEUED:
            return RunQueued(run_id=self._run_id)
        if to is RunStatus.RUNNING and previous is RunStatus.QUEUED:
            return RunStarted(run_id=self._run_id)
        if to is RunStatus.COMPLETED:
            return RunCompleted(run_id=self._run_id)
        if to is RunStatus.FAILED:
            return RunFailed(run_id=self._run_id, reason=reason)
        return None

    def _record(self, event: RunLogEvent) -> None:
        """Append an emitted event (Run or any child entity's) to the single Run log."""
        self._events.append(event)


# --- Restoration verify/reject (RUN_RESTORE_SPEC §4.3) ------------------------------------
# Structural checks of a snapshot against the invariants of RUN_SPEC.md §5. Nothing is replayed:
# the checks read the preserved state as it is (ADR-0015 §Rationale).


def _verify_snapshot(snapshot: RunSnapshot) -> None:
    """Admit ``snapshot`` only if a Run could legally hold it, else ``RunRestorationError``."""
    _verify_run_state(snapshot)
    _verify_ownership(snapshot)
    _verify_task_states(snapshot)
    _verify_references(snapshot)
    _verify_counters(snapshot)


def _verify_run_state(snapshot: RunSnapshot) -> None:
    """Exactly one status, the fixed input in place, rework within its bound (INV-01/02/08)."""
    if not isinstance(snapshot.status, RunStatus):
        raise RunRestorationError(f"status must be a RunStatus, got {snapshot.status!r}")
    fixed_input = (
        ("run_id", snapshot.run_id),
        ("content_brief_ref", snapshot.content_brief_ref),
        ("workflow_version_ref", snapshot.workflow_version_ref),
    )
    for name, value in fixed_input:
        if not isinstance(value, str) or not value.strip():
            raise RunRestorationError(f"{name} must be a non-blank reference")
    if not 0 <= snapshot.rework_count <= snapshot.rework_policy.max_rework_iterations:
        raise RunRestorationError(
            f"rework_count {snapshot.rework_count} is outside the bound of {snapshot.rework_policy}"
        )


def _verify_ownership(snapshot: RunSnapshot) -> None:
    """Every child and journal entry belongs to this Run, and no id is used twice (INV-07)."""
    owned = (
        *snapshot.tasks,
        *snapshot.artifacts,
        *snapshot.human_reviews,
        *snapshot.evaluations,
        *snapshot.analytics_records,
        *snapshot.events,
    )
    for item in owned:
        if item.run_id != snapshot.run_id:
            raise RunRestorationError(f"{item!r} belongs to another Run than {snapshot.run_id}")
    outputs = [task.output.output_id for task in snapshot.tasks if task.output is not None]
    for kind, ids in {**_child_ids(snapshot), "output": outputs}.items():
        if len(set(ids)) != len(ids):
            raise RunRestorationError(f"a {kind} id appears twice in the snapshot")


def _verify_task_states(snapshot: RunSnapshot) -> None:
    """Attempts within each Task's bound; an Output only on its own SUCCEEDED Task (INV-07/08)."""
    for task in snapshot.tasks:
        if not 0 <= task.attempt_count <= task.retry_policy.max_attempts:
            raise RunRestorationError(
                f"task {task.task_id} has {task.attempt_count} attempts, "
                f"outside the bound of {task.retry_policy}"
            )
        if task.output is not None and task.output.task_id != task.task_id:
            raise RunRestorationError(f"task {task.task_id} holds another Task's Output")
        if task.output is not None and task.status is not TaskStatus.SUCCEEDED:
            raise RunRestorationError(f"task {task.task_id} has an Output but did not succeed")


def _verify_references(snapshot: RunSnapshot) -> None:
    """Every reference between children points at a child this Run owns (INV-07)."""
    outputs = {task.output.output_id for task in snapshot.tasks if task.output is not None}
    derived_from = [artifact.output_ref for artifact in snapshot.artifacts]
    if not set(derived_from) <= outputs:
        raise RunRestorationError("an Artifact derives from an Output this Run does not own")
    if len(set(derived_from)) != len(derived_from):
        raise RunRestorationError("two Artifacts derive from one Output (Output->Artifact is 1:1)")
    artifacts = {artifact.artifact_id for artifact in snapshot.artifacts}
    subjects = {
        *(view.supersedes_ref for view in snapshot.artifacts if view.supersedes_ref is not None),
        *(review.artifact_ref for review in snapshot.human_reviews),
        *(evaluation.artifact_ref for evaluation in snapshot.evaluations),
    }
    if not subjects <= artifacts:
        raise RunRestorationError("a child refers to an Artifact this Run does not own")
    roles = {task.task_id: task.agent_ref for task in snapshot.tasks}
    for record in snapshot.analytics_records:
        if roles.get(record.task_id) != record.agent_ref:
            raise RunRestorationError(
                f"analytics record {record.record_id} does not match an owned Task and its role"
            )


def _verify_counters(snapshot: RunSnapshot) -> None:
    """Each id counter is past every id already used, so continuing reuses none (ADR-0015 I3)."""
    counters = {
        "task": snapshot.task_seq,
        "artifact": snapshot.artifact_seq,
        "review": snapshot.review_seq,
        "evaluation": snapshot.evaluation_seq,
        "analytics": snapshot.analytics_seq,
    }
    for kind, ids in _child_ids(snapshot).items():
        used = [_sequence_number(snapshot.run_id, kind, child_id) for child_id in ids]
        if counters[kind] < max([len(ids), *used]):
            raise RunRestorationError(
                f"the {kind} counter {counters[kind]} would hand out an id that is already taken"
            )


def _child_ids(snapshot: RunSnapshot) -> dict[str, list[str]]:
    """The ids of each counted kind of child, keyed by the word the Run puts in those ids."""
    return {
        "task": [task.task_id for task in snapshot.tasks],
        "artifact": [artifact.artifact_id for artifact in snapshot.artifacts],
        "review": [review.review_id for review in snapshot.human_reviews],
        "evaluation": [evaluation.evaluation_id for evaluation in snapshot.evaluations],
        "analytics": [record.record_id for record in snapshot.analytics_records],
    }


def _sequence_number(run_id: str, kind: str, child_id: str) -> int:
    """The counter value that generated ``child_id``, or 0 if the Run never generates that id."""
    number = child_id.removeprefix(f"{run_id}-{kind}-")
    if number == child_id or not (number.isascii() and number.isdigit()):
        return 0
    return int(number)
