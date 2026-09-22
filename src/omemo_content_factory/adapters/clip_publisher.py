"""Posting an approved clip to the platforms (ADR-0073).

``ClipPublisher`` hands a rendered, approved clip and its post text to whatever posts it, and
reports how that went. It is named by its role; the vendor behind it lives in ``infrastructure/``.

A post is irreversible, so the contract is built around one id the core chooses: ``submit`` is
idempotent per ``request_id``, and ``status`` answers for that same id — including
``NOT_FOUND``, which is what lets the core look before it submits instead of posting twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from omemo_content_factory.adapters.review_desk import PostDraft

__all__ = [
    "ClipPublisher",
    "ClipPublisherError",
    "PlatformResult",
    "PublishRequest",
    "PublishState",
    "PublishStatus",
]


@dataclass(frozen=True, slots=True)
class PublishRequest:
    """One clip to post: our id for the job, the file, the text and where it goes."""

    request_id: str
    video_path: str
    post: PostDraft
    platforms: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value in (("request_id", self.request_id), ("video_path", self.video_path)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a publish request needs a non-blank {name}")
        if not isinstance(self.post, PostDraft):
            raise ValueError("a publish request carries a PostDraft")
        if (
            not isinstance(self.platforms, tuple)
            or not self.platforms
            or not all(isinstance(name, str) and name.strip() for name in self.platforms)
        ):
            raise ValueError("a publish request names at least one platform")


class PublishState(Enum):
    """Where a publication stands, as the publisher reports it."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class PlatformResult:
    """How one platform took the clip. ``url`` when the platform reported one."""

    platform: str
    success: bool
    url: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PublishStatus:
    """A publication's state and, per platform, what is known so far."""

    state: PublishState
    results: tuple[PlatformResult, ...] = ()


class ClipPublisherError(Exception):
    """The publisher could not be reached or answered nonsense (technical, not a domain error)."""


class ClipPublisher(Protocol):
    """Posts a clip and reports on the post (ADR-0073 §1)."""

    def submit(self, request: PublishRequest, /) -> None:
        """Hand the clip over. Submitting the same ``request_id`` again posts nothing new."""
        ...

    def status(self, request_id: str, /) -> PublishStatus:
        """Where the job with ``request_id`` stands; ``NOT_FOUND`` if it was never submitted."""
        ...
