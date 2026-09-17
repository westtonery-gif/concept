"""Brief production — one invocation of the factory for one brief (ROADMAP Stage 11, ADR-0048).

``BriefProduction.invoke`` is what an entrypoint does each time a brief is triggered, whether a
person runs the CLI or an automation calls the service: read the reviewer's decision from the review
desk (ADR-0046), produce or resume the brief's Run (ADR-0042), publish its pending review (ADR-0044)
and show where the review is on the brief (ADR-0047). A desk that refuses, and QA that gives no
verdict, are reported in the returned ``BriefInvocation`` instead of being raised — the Run stays in
a stored state the next invocation continues from. Anything else propagates.

``waiting_briefs`` names the briefs whose Runs wait for a human, so a sweep can invoke each of them
and pick up decisions made on the desk since. Nothing here is specific to Notion, Google Docs or
the transport that triggers it.
"""

from __future__ import annotations

from dataclasses import dataclass

from omemo_content_factory.adapters.brief_board import BriefBoard
from omemo_content_factory.adapters.review_desk import ReviewDesk, ReviewDeskError
from omemo_content_factory.adapters.run_store import RunIndex
from omemo_content_factory.application.brief_intake import produce_brief
from omemo_content_factory.application.brief_status import BriefStatusReporter
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import MeasuredEvaluatorError
from omemo_content_factory.application.review_decision import (
    FetchedDecision,
    take_review_decision,
)
from omemo_content_factory.application.review_publication import (
    PublishedReview,
    publish_pending_review,
)
from omemo_content_factory.domain.run import Run, RunStatus
from omemo_content_factory.domain.workflow import Workflow

_RUN_ID_PREFIX = "run-notion-"


def run_id_for_brief(brief_ref: str) -> str:
    """The id of the Run a brief is produced as — stable, so a new invocation resumes it.

    The spelling predates this module (``demo_notion.py``) and is kept so stored Runs resume.
    """
    return f"{_RUN_ID_PREFIX}{brief_ref}"


@dataclass(frozen=True, slots=True)
class BriefInvocation:
    """What one invocation did for one brief (ADR-0048 §1).

    ``run`` is ``None`` only when there was no stored Run and no producible brief. The ``*_error``
    fields carry the message of a desk refusal or of QA giving no verdict; each leaves the stored
    Run as the next invocation expects to find it.
    """

    brief_ref: str
    run_id: str
    run: Run | None
    decision: FetchedDecision | None = None
    decision_error: str | None = None
    qa_error: str | None = None
    published: PublishedReview | None = None
    publish_error: str | None = None


class BriefProduction:
    """Produces briefs from ``board`` through ``director``, committing through ``store``.

    ``director`` must commit through ``store``, and ``store`` report to ``board``. Without a
    ``desk`` no decision is read and nothing is published; without an ``index`` there is no sweep.
    """

    def __init__(
        self,
        director: ContentDirector,
        store: BriefStatusReporter,
        board: BriefBoard,
        workflow: Workflow,
        *,
        desk: ReviewDesk | None = None,
        index: RunIndex | None = None,
    ) -> None:
        self._director = director
        self._store = store
        self._board = board
        self._workflow = workflow
        self._desk = desk
        self._index = index

    @property
    def store(self) -> BriefStatusReporter:
        """The store every invocation commits through (and reports the brief's status from)."""
        return self._store

    @property
    def has_desk(self) -> bool:
        """Whether decisions are read from and reviews published to a review desk."""
        return self._desk is not None

    def invoke(self, brief_ref: str) -> BriefInvocation:
        """Decision → production → publication → link, for ``brief_ref`` (ADR-0048 §1).

        ``BriefIntakeError``, board and store failures, domain errors and defects propagate.
        """
        run_id = run_id_for_brief(brief_ref)
        decision: FetchedDecision | None = None
        decision_error: str | None = None
        if self._desk is not None:
            try:
                decision = take_review_decision(self._store, self._desk, run_id)
            except ReviewDeskError as exc:
                decision_error = str(exc)
        qa_error: str | None = None
        try:
            run = produce_brief(
                self._director,
                self._store,
                self._board,
                self._workflow,
                brief_ref=brief_ref,
                run_id=run_id,
            )
        except MeasuredEvaluatorError as exc:
            qa_error = f"{type(exc).__name__}: {exc}"
            run = self._store.load(run_id)
        published, publish_error = self._publish(run)
        return BriefInvocation(
            brief_ref=brief_ref,
            run_id=run_id,
            run=run,
            decision=decision,
            decision_error=decision_error,
            qa_error=qa_error,
            published=published,
            publish_error=publish_error,
        )

    def waiting_briefs(self) -> tuple[str, ...]:
        """The briefs whose Runs are stored waiting for a human, in Run id order (ADR-0048 §3).

        Only Runs the brief intake created count (their id is the brief's); a Run gone between the
        listing and its load is skipped. Without an index this is a ``ValueError``.
        """
        if self._index is None:
            raise ValueError("listing waiting briefs needs a run index")
        briefs: list[str] = []
        for run_id in self._index.run_ids(status=RunStatus.WAITING_HUMAN):
            run = self._store.load(run_id)
            if run is not None and run_id_for_brief(run.content_brief_ref) == run_id:
                briefs.append(run.content_brief_ref)
        return tuple(briefs)

    def _publish(self, run: Run | None) -> tuple[PublishedReview | None, str | None]:
        if self._desk is None or run is None:
            return None, None
        try:
            published = publish_pending_review(run, self._desk)
        except ReviewDeskError as exc:
            return None, str(exc)
        if published is not None:
            self._store.show_review_location(run, published.location)
        return published, None
