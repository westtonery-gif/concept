"""In-memory stubs of the board, the review desk and the analytics sink (ADR-0025).

Stage 6 asks for a basic implementation of each adapter contract ahead of the real integrations
(Notion — Stage 9, Google Docs — Stage 10, analytics export — Stages 13–14). These are those
implementations: they keep their state in the process's memory and reach nothing outside it. Like
``FakeLLMClient`` they are infrastructure, not test doubles — the core holds only the Protocol type
and never learns which implementation it was given.

Each stub also plays the party on the other side of the wall, through a small control side that is
**not** part of its Protocol (ADR-0025 §2): the editor files briefs (``InMemoryBriefBoard.put``)
and episodes ready to clip (``InMemoryEpisodeBoard.put``), the human decides
(``InMemoryReviewDesk.decide``), and what the outside would see can be read back.

Where the contract is silent the stubs fail loudly instead of guessing (ADR-0025 §3): a status on an
unknown brief or episode, a different package under a published review, a second different
decision and a different record under a delivered id are refused with the contract's own error.
"""

from __future__ import annotations

from collections.abc import Sequence

from omemo_content_factory.adapters.analytics_sink import AnalyticsSinkError
from omemo_content_factory.adapters.brief_board import BriefBoardError, IncomingBrief
from omemo_content_factory.adapters.episode_board import EpisodeBoardError, IncomingEpisode
from omemo_content_factory.adapters.review_desk import (
    ReviewDecision,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.domain.analytics import AnalyticsRecord, AnalyticsRecordId
from omemo_content_factory.domain.human_review import ReviewId
from omemo_content_factory.domain.run import RunStatus

_REVIEW_LOCATION = "memory://reviews/{review_id}"


class InMemoryEpisodeBoard:
    """An ``EpisodeBoard`` holding episodes marked ready to clip and the statuses reported on them.

    Infrastructure like ``FakeLLMClient``, not a test double (ADR-0025). Its control side —
    ``put`` (the editor) and ``reports`` (what the outside sees) — is deliberately **outside** the
    ``EpisodeBoard`` Protocol, so the core only ever holds the contract.
    """

    def __init__(self) -> None:
        self._episodes: dict[str, IncomingEpisode] = {}
        self._ready: set[str] = set()
        self._reports: dict[str, list[tuple[str, RunStatus]]] = {}

    def put(self, episode: IncomingEpisode, *, ready: bool = True) -> None:
        """File ``episode`` (control side: the editor), replacing what its reference held."""
        self._episodes[episode.episode_ref] = episode
        if ready:
            self._ready.add(episode.episode_ref)
        else:
            self._ready.discard(episode.episode_ref)

    def fetch_episode(self, episode_ref: str, /) -> IncomingEpisode | None:
        """The episode as filed if it is ready to clip; ``None`` if unknown or not ready."""
        return self._episodes[episode_ref] if episode_ref in self._ready else None

    def report_status(self, episode_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        """Show the Run's status on its episode; repeating the last report changes nothing.

        An episode the board never had raises ``EpisodeBoardError`` and nothing is recorded — the
        contract is silent here, and ADR-0025 §3 chose to fail loudly rather than invent a row.
        """
        if episode_ref not in self._episodes:
            raise EpisodeBoardError(f"episode {episode_ref} is not on the board")
        history = self._reports.setdefault(episode_ref, [])
        if not history or history[-1] != (run_id, status):
            history.append((run_id, status))

    def reports(self, episode_ref: str) -> tuple[tuple[str, RunStatus], ...]:
        """What the board shows for ``episode_ref``: ``(run_id, status)`` reports, oldest first."""
        return tuple(self._reports.get(episode_ref, ()))


class InMemoryBriefBoard:
    """A ``BriefBoard`` holding filed briefs and the statuses reported on them."""

    def __init__(self) -> None:
        self._briefs: dict[str, IncomingBrief] = {}
        self._ready: set[str] = set()
        self._reports: dict[str, list[tuple[str, RunStatus]]] = {}
        self._locations: dict[str, list[tuple[str, str]]] = {}

    def put(self, brief: IncomingBrief, *, ready: bool = True) -> None:
        """File ``brief`` (control side: the editor), replacing what its reference held before.

        A brief filed with ``ready=False`` is on the board but not handed over until filed again
        as ready.
        """
        self._briefs[brief.brief_ref] = brief
        if ready:
            self._ready.add(brief.brief_ref)
        else:
            self._ready.discard(brief.brief_ref)

    def fetch_brief(self, brief_ref: str, /) -> IncomingBrief | None:
        """The brief as filed if it is ready for production; ``None`` if unknown or not ready."""
        return self._briefs[brief_ref] if brief_ref in self._ready else None

    def report_status(self, brief_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        """Show the Run's status on its brief; repeating the last report changes nothing.

        A brief the board never had raises ``BriefBoardError`` and nothing is recorded.
        """
        if brief_ref not in self._briefs:
            raise BriefBoardError(f"brief {brief_ref} is not on the board")
        history = self._reports.setdefault(brief_ref, [])
        if not history or history[-1] != (run_id, status):
            history.append((run_id, status))

    def reports(self, brief_ref: str) -> tuple[tuple[str, RunStatus], ...]:
        """What the board shows for ``brief_ref``: ``(run_id, status)`` reports, oldest first."""
        return tuple(self._reports.get(brief_ref, ()))

    def report_review_location(self, brief_ref: str, /, *, run_id: str, location: str) -> None:
        """Show the review's location on its brief; repeating the last report changes nothing.

        A brief the board never had, or a blank location, raises ``BriefBoardError`` and nothing is
        recorded (ADR-0047 §3).
        """
        if brief_ref not in self._briefs:
            raise BriefBoardError(f"brief {brief_ref} is not on the board")
        if not location.strip():
            raise BriefBoardError(f"a review location for run {run_id} must not be blank")
        history = self._locations.setdefault(brief_ref, [])
        if not history or history[-1] != (run_id, location):
            history.append((run_id, location))

    def review_locations(self, brief_ref: str) -> tuple[tuple[str, str], ...]:
        """Review locations shown for ``brief_ref``: ``(run_id, location)``, oldest first."""
        return tuple(self._locations.get(brief_ref, ()))


class InMemoryReviewDesk:
    """A ``ReviewDesk`` holding published packages and the decisions made on them."""

    def __init__(self) -> None:
        self._packages: dict[ReviewId, ReviewPackage] = {}
        self._decisions: dict[ReviewId, ReviewDecision] = {}

    def publish(self, package: ReviewPackage, /) -> str:
        """Put the package in front of the reviewer; return its location.

        Publishing the same package again returns the same location and keeps one copy. A
        different package under an already-published ``review_id`` raises ``ReviewDeskError`` and
        the first one stays.
        """
        published = self._packages.setdefault(package.review_id, package)
        if published != package:
            raise ReviewDeskError(
                f"review {package.review_id} is already published with another package"
            )
        return _REVIEW_LOCATION.format(review_id=package.review_id)

    def fetch_decision(self, review_id: ReviewId, /) -> ReviewDecision | None:
        """The decision on a published review, ``None`` while pending; unpublished → error."""
        self._require_published(review_id)
        return self._decisions.get(review_id)

    def decide(self, review_id: ReviewId, decision: ReviewDecision) -> None:
        """Record the human's decision (control side: the reviewer).

        The review must be published. The first decision is final: repeating it changes nothing,
        a different one raises ``ReviewDeskError``.
        """
        self._require_published(review_id)
        decided = self._decisions.setdefault(review_id, decision)
        if decided != decision:
            raise ReviewDeskError(
                f"review {review_id} is already decided: {decided.decision.value}"
            )

    def published(self, review_id: ReviewId) -> ReviewPackage | None:
        """What the reviewer sees under ``review_id``, or ``None`` if it was never published."""
        return self._packages.get(review_id)

    def _require_published(self, review_id: ReviewId) -> None:
        if review_id not in self._packages:
            raise ReviewDeskError(f"review {review_id} was never published")


class InMemoryAnalyticsSink:
    """An ``AnalyticsSink`` keeping one copy of each delivered record, in delivery order."""

    def __init__(self) -> None:
        self._records: dict[AnalyticsRecordId, AnalyticsRecord] = {}

    def export(self, records: Sequence[AnalyticsRecord], /) -> None:
        """Deliver the records; one already delivered is skipped.

        A different record under a delivered ``record_id`` (or twice within the batch) raises
        ``AnalyticsSinkError``; the batch is checked first, so a refused export delivers nothing.
        """
        batch: dict[AnalyticsRecordId, AnalyticsRecord] = {}
        for record in records:
            known = self._records.get(record.record_id, batch.get(record.record_id))
            if known is not None and known != record:
                raise AnalyticsSinkError(
                    f"record {record.record_id} was delivered with different content"
                )
            batch.setdefault(record.record_id, record)
        for record_id, record in batch.items():
            self._records.setdefault(record_id, record)

    @property
    def records(self) -> tuple[AnalyticsRecord, ...]:
        """Every delivered record, once each, in the order of first delivery."""
        return tuple(self._records.values())
