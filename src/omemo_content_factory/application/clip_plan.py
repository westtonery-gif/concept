"""Turning indexed footage into a list of clips — a pure function (CLIPPING_SPEC §6).

There is no planner agent in v1 (ADR-0058 §4): both cutting modes are arithmetic over what the
footage index reported, so this module decides everything about *which* intervals become clips and
nothing about *what they mean*. It performs no I/O, reads no clock and uses no randomness, which is
why the same episode always yields the same plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from omemo_content_factory.adapters.clip_renderer import Caption
from omemo_content_factory.adapters.episode_board import ClipMode
from omemo_content_factory.adapters.footage_index import IndexedFootage, SpeechSpan

__all__ = ["ClipPlan", "PlannedClip", "plan_clips"]


@dataclass(frozen=True, slots=True)
class PlannedClip:
    """One clip: a contiguous interval and the speech it contains (ADR-0058 §2).

    The speech is carried **twice, on purpose** (ADR-0062 §2): ``transcript`` is flat text, which
    is what the QA role reads because it judges meaning; ``captions`` are the same words with times
    relative to this clip, which is what a renderer needs to draw them when they are said.
    """

    index: int
    start_ms: int
    end_ms: int
    transcript: str
    captions: tuple[Caption, ...] = ()


@dataclass(frozen=True, slots=True)
class ClipPlan:
    """Every clip an episode yields under one mode, in episode order."""

    episode_ref: str
    mode: ClipMode
    clips: tuple[PlannedClip, ...] = ()


def plan_clips(
    indexed: IndexedFootage,
    *,
    episode_ref: str,
    mode: ClipMode,
    chunk_ms: int,
    max_ms: int,
    pause_tolerance_ms: int,
) -> ClipPlan:
    """Plan the episode's clips.

    ``chunk_ms`` is ``CHUNK``'s piece length and the length a too-long scene is split into;
    ``max_ms`` is the longest clip ``SCENE`` may emit by merging; ``pause_tolerance_ms`` is how far
    a boundary may move to land in a pause rather than mid-word.
    """
    for name, value in (
        ("chunk_ms", chunk_ms),
        ("max_ms", max_ms),
        ("pause_tolerance_ms", pause_tolerance_ms),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"plan_clips needs a whole, non-negative {name}")
    if chunk_ms <= 0 or max_ms <= 0:
        raise ValueError("plan_clips needs a positive chunk_ms and max_ms")
    if not episode_ref.strip():
        raise ValueError("plan_clips needs a non-blank episode_ref")

    bounds = (
        _chunk_bounds(indexed, chunk_ms, pause_tolerance_ms)
        if mode is ClipMode.CHUNK
        else _scene_bounds(indexed, chunk_ms, max_ms, pause_tolerance_ms)
    )
    clips = tuple(
        PlannedClip(
            index=number,
            start_ms=start,
            end_ms=end,
            transcript=_transcript(indexed.speech, start, end),
            captions=_captions(indexed.speech, start, end),
        )
        for number, (start, end) in enumerate(bounds, start=1)
    )
    return ClipPlan(episode_ref=episode_ref, mode=mode, clips=clips)


def _chunk_bounds(
    indexed: IndexedFootage, chunk_ms: int, tolerance_ms: int
) -> list[tuple[int, int]]:
    """Consecutive pieces covering the episode, each boundary nudged to a pause."""
    return _split(0, indexed.duration_ms, indexed, chunk_ms, tolerance_ms)


def _scene_bounds(
    indexed: IndexedFootage, chunk_ms: int, max_ms: int, tolerance_ms: int
) -> list[tuple[int, int]]:
    """Scenes merged while they fit under ``max_ms``; one that does not is split like a chunk.

    No detected scene means no ``SCENE`` clips. That is deliberately not an error and deliberately
    not "treat the whole episode as one scene": the service found nothing, and guessing here would
    be a second copy of a decision that is not ours (CLIPPING_ACCEPTANCE CLP-06).
    """
    if not indexed.scenes:
        return []
    edges = [0, *(scene.at_ms for scene in indexed.scenes), indexed.duration_ms]
    scenes = [(start, end) for start, end in pairwise(edges) if end > start]

    bounds: list[tuple[int, int]] = []
    open_start: int | None = None
    open_end = 0
    for start, end in scenes:
        if end - start > max_ms:
            if open_start is not None:
                bounds.append((open_start, open_end))
                open_start = None
            bounds.extend(_split(start, end, indexed, chunk_ms, tolerance_ms))
            continue
        if open_start is None:
            open_start, open_end = start, end
        elif end - open_start <= max_ms:
            open_end = end
        else:
            bounds.append((open_start, open_end))
            open_start, open_end = start, end
    if open_start is not None:
        bounds.append((open_start, open_end))
    return bounds


def _split(
    start: int, end: int, indexed: IndexedFootage, chunk_ms: int, tolerance_ms: int
) -> list[tuple[int, int]]:
    """Cut ``[start, end)`` into pieces of ``chunk_ms``, each cut nudged to the nearest pause."""
    cuts = [start]
    while cuts[-1] + chunk_ms < end:
        ideal = cuts[-1] + chunk_ms
        nudged = _nudge(ideal, indexed.speech, tolerance_ms)
        cuts.append(nudged if nudged > cuts[-1] else ideal)
    cuts.append(end)
    return [(a, b) for a, b in pairwise(cuts) if b > a]


def _nudge(at_ms: int, speech: tuple[SpeechSpan, ...], tolerance_ms: int) -> int:
    """Move a boundary out of a spoken line, if a pause is close enough.

    A boundary already in a pause is left alone; one inside a line moves to whichever end of that
    line is nearer, and only when that is within the tolerance. Otherwise it stays — the rule
    improves a cut, it never refuses to produce one (CLIPPING_ACCEPTANCE CLP-03).
    """
    for span in speech:
        if span.start_ms < at_ms < span.end_ms:
            before, after = at_ms - span.start_ms, span.end_ms - at_ms
            nearer = span.start_ms if before <= after else span.end_ms
            return nearer if min(before, after) <= tolerance_ms else at_ms
    return at_ms


def _captions(speech: tuple[SpeechSpan, ...], start_ms: int, end_ms: int) -> tuple[Caption, ...]:
    """The overlapping lines, re-based to the clip and clipped to its bounds (ADR-0062 §2).

    A line straddling a boundary is shortened, never dropped: its words are partly spoken on
    screen, and losing dialogue at every cut is exactly where a viewer would notice.
    """
    return tuple(
        Caption(
            start_ms=max(span.start_ms, start_ms) - start_ms,
            end_ms=min(span.end_ms, end_ms) - start_ms,
            text=span.text.strip(),
        )
        for span in speech
        if span.start_ms < end_ms and span.end_ms > start_ms and span.text.strip()
    )


def _transcript(speech: tuple[SpeechSpan, ...], start_ms: int, end_ms: int) -> str:
    """The text of every line that overlaps ``[start_ms, end_ms)``, in order."""
    return " ".join(
        span.text.strip() for span in speech if span.start_ms < end_ms and span.end_ms > start_ms
    ).strip()
