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
class Caption:
    """One line to draw, timed **relative to the clip's own start** (ADR-0062 §1).

    ``0`` is the clip's first frame, not the episode's: the renderer is handed a clip and the lines
    to draw on it, and never learns where that clip sat in the episode.
    """

    start_ms: int
    end_ms: int
    text: str

    def __post_init__(self) -> None:
        for name, value in (("start_ms", self.start_ms), ("end_ms", self.end_ms)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"a caption needs a whole, non-negative {name}")
        if self.end_ms <= self.start_ms:
            raise ValueError("a caption must end after it starts")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("a caption needs non-blank text")


@dataclass(frozen=True, slots=True)
class ClipRenderRequest:
    """One clip to cut: where from, which interval, which lines to burn in, where to write.

    ``captions`` may be empty, which means burn nothing — a clip with no speech is a normal clip.
    How captions become pixels is the adapter's business; no subtitle file format is in this
    contract, because a different renderer would use a different one (ADR-0062 §3).
    """

    located: LocatedEpisode
    start_ms: int
    end_ms: int
    captions: tuple[Caption, ...]
    destination: str

    def __post_init__(self) -> None:
        for name, value in (("start_ms", self.start_ms), ("end_ms", self.end_ms)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"a clip render request needs a whole, non-negative {name}")
        if self.end_ms <= self.start_ms:
            raise ValueError("a clip must end after it starts")
        if not isinstance(self.destination, str) or not self.destination.strip():
            raise ValueError("a clip render request needs a non-blank destination")
        if not isinstance(self.captions, tuple) or not all(
            isinstance(caption, Caption) for caption in self.captions
        ):
            raise ValueError("a clip render request keeps its captions in a tuple")
        if any(caption.end_ms > self.end_ms - self.start_ms for caption in self.captions):
            raise ValueError("a caption falls outside the clip it belongs to")


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


@dataclass(frozen=True, slots=True)
class FinishRequest:
    """The author's framing for a rendered clip: a headline above it, a footer below (ADR-0078)."""

    source_path: str
    headline: str
    footer: str | None
    destination: str

    def __post_init__(self) -> None:
        for name, value in (
            ("source_path", self.source_path),
            ("headline", self.headline),
            ("destination", self.destination),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a finish request needs a non-blank {name}")
        if self.footer is not None and not self.footer.strip():
            raise ValueError("a footer must not be blank when given")


class ClipFinisher(Protocol):
    """Adds the author's framing to a rendered clip, into the bars of its canvas (ADR-0078)."""

    def finish(self, request: FinishRequest, /) -> RenderedClip:
        """Write the framed clip to ``request.destination`` and return what it actually is."""
        ...
