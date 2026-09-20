"""Cutting the clip and writing the file (CLIPPING_SPEC §7).

Rendering is mechanical: an interval, burnt-in subtitles, a file. No model decides anything here
(ADR-0053 §1, ADR-0058 §4), so it is a deterministic ``Workflow`` step behind this port.

``RenderedClip`` carries the renderer's **own measurements**. It produced the file and knows them,
and that is what lets the format check be arithmetic instead of a question for a model
(ADR-0056 §1): a clip out of spec is the renderer doing other than it was told, which is a
``FAILED`` Task with a stable reason, not a ``flagged`` verdict for a human to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.adapters.episode_source import LocatedEpisode


@dataclass(frozen=True, slots=True)
class ClipRenderRequest:
    """One clip to cut: where from, which interval, what to burn in, where to write."""

    located: LocatedEpisode
    start_ms: int
    end_ms: int
    subtitles: str
    destination: str

    def __post_init__(self) -> None:
        for name, value in (("start_ms", self.start_ms), ("end_ms", self.end_ms)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"a clip render request needs a whole, non-negative {name}")
        if self.end_ms <= self.start_ms:
            raise ValueError("a clip must end after it starts")
        if not isinstance(self.destination, str) or not self.destination.strip():
            raise ValueError("a clip render request needs a non-blank destination")
        if not isinstance(self.subtitles, str):
            raise ValueError("a clip render request needs subtitles text, possibly empty")


@dataclass(frozen=True, slots=True)
class RenderedClip:
    """The file that was written, with the measurements the format check reads."""

    path: str
    duration_ms: int
    width: int
    height: int
    container: str

    def __post_init__(self) -> None:
        for name in ("duration_ms", "width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"a rendered clip needs a positive {name}")
        for name in ("path", "container"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a rendered clip needs a non-blank {name}")


class ClipRendererError(Exception):
    """The clip could not be rendered (technical failure, not a ``DomainError``)."""


class ClipRenderer(Protocol):
    """Cuts one clip out of an episode and reports what it produced."""

    def render(self, request: ClipRenderRequest, /) -> RenderedClip:
        """Write the clip and return its path and measurements."""
        ...
