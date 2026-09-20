"""Where an episode's video actually is (ADR-0053 §7, CLIPPING_SPEC §4).

The board says *which* episode; this port says *where the bytes are*. Keeping them apart is what
lets the video move to remote storage later without touching the board: a remote implementation
materialises a local copy and returns its path, so the render step never learns about the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LocatedEpisode:
    """A source handle resolved to a **locally readable** file."""

    source_ref: str
    path: str

    def __post_init__(self) -> None:
        for name, value in (("source_ref", self.source_ref), ("path", self.path)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a located episode needs a non-blank {name}")


class EpisodeSourceError(Exception):
    """The source could not be read (technical failure, not a ``DomainError``)."""


class EpisodeSource(Protocol):
    """Resolves an opaque ``source_ref`` to a file the rest of the department can open."""

    def locate(self, source_ref: str, /) -> LocatedEpisode | None:
        """Where the episode is, or ``None`` when nothing answers to that handle."""
        ...
