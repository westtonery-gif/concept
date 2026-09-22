"""Understanding an episode: scene boundaries and timed speech (ADR-0053 §3, CLIPPING_SPEC §5).

The vendor (Vyra AI) lives in ``infrastructure/`` and never reaches this contract — the port is
named by its role, like every other outside system here (ADR-0023).

Indexing takes minutes, so it is a ``Workflow`` step behind the ADR-0049 queue and **never** a call
a model waits on (ADR-0053 §4). In v1 no Tool reaches this port at all: with no planner agent
(ADR-0058 §4) a Workflow step calls it through an ordinary Adapter, so the side-effecting-Tool
boundary of ADR-0054 is not exercised here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.adapters.episode_source import LocatedEpisode


@dataclass(frozen=True, slots=True)
class SceneBreak:
    """Where one scene ends and the next begins, in milliseconds from the start."""

    at_ms: int

    def __post_init__(self) -> None:
        if isinstance(self.at_ms, bool) or not isinstance(self.at_ms, int) or self.at_ms < 0:
            raise ValueError("a scene break needs a whole, non-negative millisecond offset")


@dataclass(frozen=True, slots=True)
class SpeechSpan:
    """One stretch of speech with its text, in milliseconds from the start."""

    start_ms: int
    end_ms: int
    text: str

    def __post_init__(self) -> None:
        for name, value in (("start_ms", self.start_ms), ("end_ms", self.end_ms)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"a speech span needs a whole, non-negative {name}")
        if self.end_ms <= self.start_ms:
            raise ValueError("a speech span must end after it starts")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("a speech span needs non-blank text")


@dataclass(frozen=True, slots=True)
class SkipZone:
    """A stretch no clip may cover — opening titles, end credits (ADR-0074), in milliseconds."""

    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        for name, value in (("start_ms", self.start_ms), ("end_ms", self.end_ms)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"a skip zone needs a whole, non-negative {name}")
        if self.end_ms <= self.start_ms:
            raise ValueError("a skip zone must end after it starts")


@dataclass(frozen=True, slots=True)
class IndexedFootage:
    """What the service understood: how long the episode is, where scenes break, who says what.

    ``scenes`` are strictly increasing and inside the episode; ``speech`` is ordered and
    non-overlapping. Both may be empty — an episode with no detected scene simply yields no
    ``SCENE`` clips, which is not an error (CLIPPING_ACCEPTANCE CLP-06). ``skips`` are the
    stretches no clip may cover, ordered and non-overlapping (ADR-0074).
    """

    duration_ms: int
    scenes: tuple[SceneBreak, ...] = ()
    speech: tuple[SpeechSpan, ...] = ()
    skips: tuple[SkipZone, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or self.duration_ms <= 0
        ):
            raise ValueError("indexed footage needs a positive duration")
        if not all(isinstance(items, tuple) for items in (self.scenes, self.speech, self.skips)):
            raise ValueError("indexed footage keeps scenes, speech and skips in tuples")
        self._verify_scenes()
        self._verify_speech()
        self._verify_skips()

    def _verify_skips(self) -> None:
        end = 0
        for zone in self.skips:
            if not isinstance(zone, SkipZone):
                raise ValueError("every skip zone must be a SkipZone")
            if zone.start_ms < end:
                raise ValueError("skip zones must be ordered and must not overlap")
            if zone.end_ms > self.duration_ms:
                raise ValueError("a skip zone falls outside the episode")
            end = zone.end_ms

    def _verify_scenes(self) -> None:
        previous = -1
        for scene in self.scenes:
            if not isinstance(scene, SceneBreak):
                raise ValueError("every scene break must be a SceneBreak")
            if scene.at_ms <= previous:
                raise ValueError("scene breaks must be strictly increasing")
            if scene.at_ms > self.duration_ms:
                raise ValueError("a scene break falls outside the episode")
            previous = scene.at_ms

    def _verify_speech(self) -> None:
        end = 0
        for span in self.speech:
            if not isinstance(span, SpeechSpan):
                raise ValueError("every speech span must be a SpeechSpan")
            if span.start_ms < end:
                raise ValueError("speech spans must be ordered and must not overlap")
            if span.end_ms > self.duration_ms:
                raise ValueError("a speech span falls outside the episode")
            end = span.end_ms


class FootageIndexError(Exception):
    """The footage could not be indexed (technical failure, not a ``DomainError``)."""


class FootageIndex(Protocol):
    """Turns an episode file into scene boundaries and timed speech."""

    def index(self, located: LocatedEpisode, /) -> IndexedFootage:
        """Analyse the episode. Slow by nature — a Workflow step, never a reasoning-step call."""
        ...
