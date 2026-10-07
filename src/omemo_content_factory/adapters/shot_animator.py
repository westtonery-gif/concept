"""Bringing one picture to life for a few seconds (ADR-0090).

``VideoGenerator`` (ADR-0066) animates **from one frame to a second one**; a story shot is one
picture and a description of what moves, so it needs a different request: a frame, a prompt, a
duration. The job life cycle is the same and its types are reused — ``VideoJob``, ``VideoJobState``,
``VideoJobResult``, ``GeneratedVideo`` — so whatever polls a video job polls this one.

``submit`` is **not repeatable**: the vendor offers no key that makes a second ``submit`` the same
job, so a repeat is a second paid clip. Whoever calls it commits the returned job before anything
else, and a submit that raised without an answer is **not** retried — it may have been accepted
(ADR-0066 §3). ``collect`` is repeatable: one status ask, and the file on completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.adapters.video_generator import (
    GeneratedVideo,
    VideoGeneratorError,
    VideoJob,
    VideoJobResult,
    VideoJobState,
)

__all__ = [
    "AnimationRequest",
    "GeneratedVideo",
    "ShotAnimator",
    "VideoGeneratorError",
    "VideoJob",
    "VideoJobResult",
    "VideoJobState",
]


@dataclass(frozen=True, slots=True)
class AnimationRequest:
    """One clip: from this picture, doing this, for this many whole seconds, in this ratio."""

    frame: str
    prompt: str
    duration_s: int
    ratio: str = "9:16"

    def __post_init__(self) -> None:
        for name in ("frame", "prompt"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"an animation request needs a non-blank {name}")
        if (
            isinstance(self.duration_s, bool)
            or not isinstance(self.duration_s, int)
            or self.duration_s <= 0
        ):
            raise ValueError("an animation request needs a positive whole duration_s")
        width, colon, height = (
            self.ratio.partition(":") if isinstance(self.ratio, str) else ("", "", "")
        )
        if not (colon and width.isdecimal() and height.isdecimal() and int(width) and int(height)):
            raise ValueError("an animation request needs a ratio like 9:16")


class ShotAnimator(Protocol):
    """Starts an animation job for one picture and, later, collects its result."""

    def submit(self, request: AnimationRequest, /) -> VideoJob:
        """Start the job. Not repeatable — commit the returned job before anything else."""
        ...

    def collect(self, job: VideoJob, destination: str, /) -> VideoJobResult:
        """Ask once where the job stands; on completion write the file to ``destination``."""
        ...
