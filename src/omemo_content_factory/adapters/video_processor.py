"""Required processing of a finished cut, before QA/delivery (ADR-0095)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.adapters.clip_renderer import RenderedClip


@dataclass(frozen=True, slots=True)
class VideoProcessingRequest:
    request_id: str
    source_path: str
    destination: str

    def __post_init__(self) -> None:
        for name in ("request_id", "source_path", "destination"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"video processing needs a non-blank {name}")


@dataclass(frozen=True, slots=True)
class ProcessedVideo:
    clip: RenderedClip
    source_sha256: str
    output_sha256: str
    report_path: str

    def __post_init__(self) -> None:
        for digest in (self.source_sha256, self.output_sha256):
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("video processing needs SHA-256 hex digests")
        if self.source_sha256 == self.output_sha256:
            raise ValueError("processed video must have a different hash")
        if not self.report_path.strip():
            raise ValueError("video processing needs a report path")


class VideoProcessingError(Exception):
    """The processing gate could not be completed; the raw video must not ship."""


class VideoProcessor(Protocol):
    def process(self, request: VideoProcessingRequest, /) -> ProcessedVideo:
        """Return a verified result, reusing the same request's committed files on retry."""
        ...
