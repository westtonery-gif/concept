"""Animating from the reference photo to the ending frame (ADR-0066 §3, GENERATION_SPEC §3).

Video generation is asynchronous at the vendor and **its submission cannot be made idempotent**,
so this port does not hide polling behind one blocking call. ``submit`` starts the job and is the
department's one non-repeatable call: the Workflow commits the returned ``VideoJob`` before it
asks anything else, and a submission whose outcome was never committed is failed, never repeated.
``collect`` is one status check and may be repeated freely; on completion it downloads the file
into the caller's destination and reports the adapter's own measurements of it.

Local paths in, local paths out. No vendor URL, request shape or model name reaches this contract
(ADR-0066 §4); the job id is opaque to the core.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VideoGenerationRequest:
    """One video to make: from the first frame to the last, told how, lasting how long."""

    first_frame: str
    last_frame: str
    prompt: str
    duration_s: int

    def __post_init__(self) -> None:
        for name in ("first_frame", "last_frame", "prompt"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a video generation request needs a non-blank {name}")
        if (
            isinstance(self.duration_s, bool)
            or not isinstance(self.duration_s, int)
            or self.duration_s <= 0
        ):
            raise ValueError("a video generation request needs a positive whole duration_s")


@dataclass(frozen=True, slots=True)
class VideoJob:
    """A submitted job, named by an id only the adapter understands."""

    job_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError("a video job needs a non-blank job_id")


class VideoJobState(Enum):
    """Where a job stands. ``REJECTED`` is a content refusal, not a technical failure."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class GeneratedVideo:
    """The file that was written, with the measurements read back from it."""

    path: str
    duration_ms: int
    width: int
    height: int
    container: str

    def __post_init__(self) -> None:
        for name in ("duration_ms", "width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"a generated video needs a positive {name}")
        for name in ("path", "container"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a generated video needs a non-blank {name}")


@dataclass(frozen=True, slots=True)
class VideoJobResult:
    """One answer to ``collect``: the state, and the video exactly when it is ``COMPLETED``."""

    state: VideoJobState
    video: GeneratedVideo | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, VideoJobState):
            raise ValueError("a video job result needs a VideoJobState")
        if (self.state is VideoJobState.COMPLETED) != (self.video is not None):
            raise ValueError("a video job result carries a video exactly when it is completed")
        if self.video is not None and not isinstance(self.video, GeneratedVideo):
            raise ValueError("a video job result's video must be a GeneratedVideo")


class VideoGeneratorError(Exception):
    """The job could not be submitted or asked about (technical failure, not a ``DomainError``)."""


class VideoGenerator(Protocol):
    """Starts a video job and, later, collects its result."""

    def submit(self, request: VideoGenerationRequest, /) -> VideoJob:
        """Start the job. Not repeatable — commit the returned job before anything else."""
        ...

    def collect(self, job: VideoJob, destination: str, /) -> VideoJobResult:
        """Ask once where the job stands; on completion write the file to ``destination``."""
        ...
