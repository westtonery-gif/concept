"""The episode board contract — which episode is ready to clip (ADR-0055, CLIPPING_SPEC §3).

``EpisodeBoard`` is the editorial board the clipping department works for, named by its role and
not by the tool behind it (DOMAIN_MODEL §8). It is a **second** port beside ``BriefBoard``, not an
extension of it: a brief carries a body of text, an episode carries a source handle and a cutting
mode, and one port serving both would push ``None``-shaped fields into every implementation.

Readiness is the board's rule and is never re-decided downstream (``n8n/README.md``): an episode
that is unknown, archived, not ready or unusable is the same ``None``, deliberately
indistinguishable, exactly as ``BriefBoard.fetch_brief`` answers for a brief.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from omemo_content_factory.domain.run import RunStatus


class ClipMode(Enum):
    """How an episode is cut. The editor chooses; no agent infers it (ADR-0058 §1)."""

    CHUNK = "chunk"
    """Consecutive pieces of a configured length, each boundary nudged to a speech pause.

    For a series that follows essentially one thread, with the main characters on screen
    throughout: the episode is continuous, so length is enough and nothing needs understanding.
    """

    SCENE = "scene"
    """Boundaries are the detected scene boundaries, consecutive scenes merged under a maximum.

    For a series whose many main characters have their scenes **interleaved** through the episode:
    cutting that by length yields two minutes of unrelated threads, while a scene boundary never
    falls mid-scene and a scene belongs to one thread.
    """


@dataclass(frozen=True, slots=True)
class IncomingEpisode:
    """An episode the board handed over for clipping: editorial intent, never the video.

    ``episode_ref`` is the board's opaque reference and becomes the Run's brief reference;
    ``source_ref`` is an opaque handle an ``EpisodeSource`` resolves to something readable, so the
    board survives the video moving off a local file; ``mode`` is how this episode is cut.

    Clip length, clip count and target platform are deliberately **not** here: they are
    configuration, not a column an editor fills in for every episode (ADR-0055 §2).
    """

    episode_ref: str
    source_ref: str
    mode: ClipMode

    def __post_init__(self) -> None:
        for name, value in (("episode_ref", self.episode_ref), ("source_ref", self.source_ref)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"an incoming episode needs a non-blank {name}")
        if not isinstance(self.mode, ClipMode):
            raise ValueError(f"an incoming episode needs a ClipMode, got {self.mode!r}")


class EpisodeBoardError(Exception):
    """The board could not be read or written (technical failure, not a ``DomainError``)."""


class EpisodeBoard(Protocol):
    """Where episodes ready to clip come from and where their Runs' progress is shown."""

    def fetch_episode(self, episode_ref: str, /) -> IncomingEpisode | None:
        """The episode under ``episode_ref`` if it is ready to clip, otherwise ``None``.

        ``None`` covers unknown, archived, not-yet-ready and unusable alike; the core then starts
        no Run.
        """
        ...

    def report_status(self, episode_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        """Show the Run's current status on its episode.

        Reporting the same status again is harmless, and a failed report never changes the Run.
        """
        ...
