"""Status write-back — every committed Run status is shown on its brief (ADR-0041).

``BriefStatusReporter`` is itself a ``RunStore``: it wraps the real store and, after each save,
shows the Run's status on the board the brief came from. The Content Director already saves at
every status change (ADR-0026 §2), so it is left untouched and keeps knowing only ``RunStore``.

The store is the truth and the board its showcase: a report follows the save, never precedes it,
and a board outage (``BriefBoardError``) is logged and retried by ``sync`` instead of stopping
production. Any other exception from the board is a defect and propagates.

Once a review is published, the reporter also shows where it is (``show_review_location``,
ADR-0047). A location already shown is not sent again: a page write re-triggers an automation that
polls the page for edits (ROADMAP Stage 11), so a call with nothing new must write nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from omemo_content_factory.adapters.brief_board import BriefBoard, BriefBoardError
from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.domain.run import Run, RunStatus

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FailedReport:
    """A status the board refused to show: which Run, which status, and the board's message."""

    run_id: str
    status: RunStatus
    message: str


@dataclass(frozen=True, slots=True)
class FailedLocationReport:
    """A review location the board refused to show: which Run, which location, and the message."""

    run_id: str
    location: str
    message: str


class BriefStatusReporter:
    """A ``RunStore`` that shows each newly saved Run status on the Run's brief (ADR-0041 §2).

    The Run's ``content_brief_ref`` must be a reference on ``board`` — true of a Run created from
    ``board.fetch_brief``. What was attempted and what was shown are remembered per ``run_id`` for
    this reporter's lifetime only; a new reporter shows the current status again, which the
    contract makes harmless.
    """

    def __init__(self, store: RunStore, board: BriefBoard) -> None:
        self._store = store
        self._board = board
        self._attempted: dict[str, RunStatus] = {}
        self._shown: dict[str, RunStatus] = {}
        self._failed: list[FailedReport] = []
        self._shown_locations: dict[str, str] = {}
        self._failed_locations: list[FailedLocationReport] = []

    def save(self, run: Run, /) -> None:
        """Save through the wrapped store, then show the status once, when it has changed.

        A failed save raises the store's error and shows nothing. A status whose report was
        refused is not tried again by later saves in the same status — only by :meth:`sync`, or
        superseded by the next status (ADR-0041 §3).
        """
        self._store.save(run)
        if self._attempted.get(run.run_id) is not run.status:
            self._report(run)

    def load(self, run_id: str, /) -> Run | None:
        """Load through the wrapped store, unchanged."""
        return self._store.load(run_id)

    def sync(self, run: Run) -> None:
        """Show the Run's current status unless it is already shown; nothing is saved.

        This is the retry of a refused report (ADR-0041 §3, §4).
        """
        if self._shown.get(run.run_id) is not run.status:
            self._report(run)

    def _report(self, run: Run) -> None:
        """Ask the board to show the status; a ``BriefBoardError`` is logged and kept."""
        status = run.status
        self._attempted[run.run_id] = status
        try:
            self._board.report_status(run.content_brief_ref, run_id=run.run_id, status=status)
        except BriefBoardError as exc:
            self._failed.append(FailedReport(run.run_id, status, str(exc)))
            _LOG.warning(
                "could not show status %s of run %s on its brief: %s",
                status.value,
                run.run_id,
                exc,
            )
            return
        self._shown[run.run_id] = status

    def show_review_location(self, run: Run, location: str) -> None:
        """Show where ``run``'s latest review is, unless this reporter already showed it (§4).

        Nothing is saved and the Run is not touched. A ``BriefBoardError`` is logged and kept in
        :attr:`failed_location_reports`, and the next call tries again; any other exception
        propagates.
        """
        if self._shown_locations.get(run.run_id) == location:
            return
        try:
            self._board.report_review_location(
                run.content_brief_ref, run_id=run.run_id, location=location
            )
        except BriefBoardError as exc:
            self._failed_locations.append(FailedLocationReport(run.run_id, location, str(exc)))
            _LOG.warning(
                "could not show the review location of run %s on its brief: %s", run.run_id, exc
            )
            return
        self._shown_locations[run.run_id] = location

    def shown_review_location(self, run_id: str) -> str | None:
        """The review location this reporter last showed successfully for ``run_id``, if any."""
        return self._shown_locations.get(run_id)

    @property
    def failed_location_reports(self) -> tuple[FailedLocationReport, ...]:
        """Every refused review-location report so far, oldest first."""
        return tuple(self._failed_locations)

    def shown_status(self, run_id: str) -> RunStatus | None:
        """The status this reporter last showed successfully for ``run_id``, if any."""
        return self._shown.get(run_id)

    @property
    def failed_reports(self) -> tuple[FailedReport, ...]:
        """Every refused report so far, oldest first (including ones a later retry fixed)."""
        return tuple(self._failed)
