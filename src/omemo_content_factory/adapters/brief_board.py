"""The Notion Adapter contract — briefs in, statuses out (ADR-0023 §6).

``BriefBoard`` is the editorial board the core works for: it hands over a brief that is ready for
production and shows how the brief's Run is going. It is named by its role, not by the tool behind
it (DOMAIN_MODEL §8). ``IncomingBrief`` is what the core reads from the board — deliberately not the
Content Brief entity, which does not exist yet (ROADMAP Stage 9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.domain.run import RunStatus


@dataclass(frozen=True, slots=True)
class IncomingBrief:
    """A brief the board handed over for production: its reference and its text.

    ``brief_ref`` is the board's opaque reference and becomes the Run's ``content_brief_ref``;
    ``body`` is the brief as the core hands it to the first step. Both are non-blank.
    """

    brief_ref: str
    body: str

    def __post_init__(self) -> None:
        for name, value in (("brief_ref", self.brief_ref), ("body", self.body)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"an incoming brief needs a non-blank {name}")


class BriefBoardError(Exception):
    """The board could not be read or written (technical failure, not a ``DomainError``)."""


class BriefBoard(Protocol):
    """Where briefs come from and where their Runs' progress is shown (ADAPTER_SPEC §5)."""

    def fetch_brief(self, brief_ref: str, /) -> IncomingBrief | None:
        """The brief under ``brief_ref`` if it is ready for production, otherwise ``None``.

        ``None`` covers both an unknown and a not-yet-ready brief; the core then starts no Run.
        """
        ...

    def report_status(self, brief_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        """Show the Run's current status on its brief.

        Reporting the same status again is harmless, and a failed report never changes the Run.
        """
        ...
