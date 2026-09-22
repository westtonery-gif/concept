"""The Google Docs Adapter contract — where a human reviews a candidate and decides (ADR-0023 §7).

``ReviewDesk`` carries a candidate Artifact, with the context of the decision, to the place where a
person reviews it, and carries the person's decision back. It never decides anything itself and
never touches the Run: the core applies a fetched decision through ``Run.submit_review`` as the
Human Reviewer, and approval still needs a passed QA verdict (ADR-0007, ADR-0018). It is named by
its role, not by the editor behind it (DOMAIN_MODEL §8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.domain.artifact import ArtifactStatus, ArtifactView
from omemo_content_factory.domain.human_review import ReviewId, ReviewStatus

POST_TITLE_LIMIT = 100
"""The longest title a post may carry — YouTube's limit, the strictest target (ADR-0072 §3)."""


@dataclass(frozen=True, slots=True)
class PostDraft:
    """The text a post goes out with: a title and a description (ADR-0072 §3).

    Both are non-blank, and the title is at most :data:`POST_TITLE_LIMIT` characters.
    """

    title: str
    description: str

    def __post_init__(self) -> None:
        for name, value in (("title", self.title), ("description", self.description)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a post needs a non-blank {name}")
        if len(self.title) > POST_TITLE_LIMIT:
            raise ValueError(f"a post title is at most {POST_TITLE_LIMIT} characters")


@dataclass(frozen=True, slots=True)
class ReviewPackage:
    """What the reviewer is shown: the candidate plus the context of the decision (ARCH §13).

    ``candidate`` must be a ``CANDIDATE`` Artifact of the same Run; ``brief`` is the brief's text;
    ``qa_flags`` are the QA remarks to show, possibly none; ``post`` is a proposed post text the
    reviewer may correct, if there is one (ADR-0072). An ill-formed package never reaches a
    reviewer.
    """

    run_id: str
    review_id: ReviewId
    candidate: ArtifactView
    brief: str
    qa_flags: tuple[str, ...] = ()
    post: PostDraft | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("review_id", self.review_id),
            ("brief", self.brief),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a review package needs a non-blank {name}")
        if self.candidate.run_id != self.run_id:
            raise ValueError("the candidate belongs to another Run")
        if self.candidate.status is not ArtifactStatus.CANDIDATE:
            raise ValueError(f"only a candidate goes to review, got {self.candidate.status.value}")
        if not isinstance(self.qa_flags, tuple) or not all(
            isinstance(flag, str) and flag.strip() for flag in self.qa_flags
        ):
            raise ValueError("qa_flags must be a tuple of non-blank strings")
        if self.post is not None and not isinstance(self.post, PostDraft):
            raise ValueError("a review package's post must be a PostDraft")


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    """The human's decision as the desk read it: a terminal outcome and an optional reason.

    ``reason`` carries a rejection's reason or the requested changes; it is ``None`` or non-blank.
    The pair maps one-to-one onto ``Run.submit_review``. ``post`` is the post text as the reviewer
    left it when deciding, from a desk that lets them edit it; ``None`` otherwise (ADR-0072 §3).
    """

    decision: ReviewStatus
    reason: str | None = None
    post: PostDraft | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ReviewStatus) or self.decision is ReviewStatus.PENDING:
            raise ValueError(f"a review decision must be a terminal outcome, got {self.decision!r}")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason.strip()
        ):
            raise ValueError("a decision's reason must not be blank when given")
        if self.post is not None and not isinstance(self.post, PostDraft):
            raise ValueError("a decision's post must be a PostDraft")


class ReviewDeskError(Exception):
    """The desk could not publish or read (technical failure, not a ``DomainError``)."""


class ReviewDesk(Protocol):
    """Where a candidate is reviewed and the human decision is read back (ADAPTER_SPEC §6)."""

    def publish(self, package: ReviewPackage, /) -> str:
        """Put the package in front of the reviewer; return where it can be found (non-blank).

        Publishing the same ``review_id`` again returns the same place instead of a second copy.
        """
        ...

    def fetch_decision(self, review_id: ReviewId, /) -> ReviewDecision | None:
        """The human's decision on a published review, or ``None`` while it is still pending.

        A ``review_id`` that was never published raises ``ReviewDeskError``.
        """
        ...
