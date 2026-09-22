"""Finding the audio two episodes share — the opening titles and end credits (ADR-0074 §2).

Pure functions over Chromaprint's raw fingerprints (``fpcalc -raw``): one 32-bit value per
~0.124 s of audio. Two episodes of a series play the same theme, so their fingerprints agree there,
at some offset. The offsets are found by voting — every value the two share proposes one — and at
each strong offset the aligned values are compared bit by bit: a long run of near-equal values is
shared audio.

What counts as titles rather than a recurring cue inside the story is decided here too: long enough,
and near an end of the episode.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

__all__ = ["ITEM_MS", "shared_zones", "title_zones"]

ITEM_MS = 124.055
"""How much audio one raw Chromaprint value covers, in milliseconds."""

_MAX_BIT_ERRORS = 10
"""Of 32 bits, how many may differ for two values to count as the same audio."""

_MAX_GAP_MS = 2_000
"""A run of shared audio survives a disagreement this short (a dub line over the theme)."""

_CANDIDATE_OFFSETS = 8
"""How many of the best-voted offsets are examined."""

MIN_TITLE_MS = 15_000
"""Shorter shared audio is a recurring cue inside the story, not titles (ADR-0074 §2)."""

EDGE_FRACTION = 0.25
"""Titles live within this fraction of the episode's start or end."""


def shared_zones(
    ours: Sequence[int], theirs: Sequence[int], *, min_ms: float = 8_000
) -> list[tuple[int, int]]:
    """Every stretch of ``ours`` that ``theirs`` also contains, in milliseconds of ``ours``."""
    where: dict[int, list[int]] = defaultdict(list)
    for index, value in enumerate(theirs):
        where[value].append(index)
    votes: Counter[int] = Counter()
    for index, value in enumerate(ours):
        for other in where.get(value, ()):
            votes[index - other] += 1
    zones: list[tuple[int, int]] = []
    for offset, _ in votes.most_common(_CANDIDATE_OFFSETS):
        zones.extend(_runs(ours, theirs, offset, min_ms))
    return _merged(zones)


def title_zones(
    ours: Sequence[int], siblings: Sequence[Sequence[int]], *, duration_ms: int
) -> list[tuple[int, int]]:
    """The shared stretches of ``ours`` that are titles: long, and near an end (ADR-0074 §2)."""
    head = duration_ms * EDGE_FRACTION
    tail = duration_ms * (1 - EDGE_FRACTION)
    zones: list[tuple[int, int]] = []
    for theirs in siblings:
        for start, end in shared_zones(ours, theirs, min_ms=MIN_TITLE_MS):
            if end <= head or start >= tail:
                zones.append((max(0, start), min(end, duration_ms)))
    return [(start, end) for start, end in _merged(zones) if end > start]


def _runs(
    ours: Sequence[int], theirs: Sequence[int], offset: int, min_ms: float
) -> list[tuple[int, int]]:
    """Runs of near-equal aligned values at one offset, allowing short disagreements."""
    max_gap = _MAX_GAP_MS / ITEM_MS
    min_items = min_ms / ITEM_MS
    runs: list[tuple[int, int]] = []
    start: int | None = None
    last = 0
    for index in range(max(0, offset), min(len(ours), len(theirs) + offset)):
        if _close(ours[index], theirs[index - offset]):
            if start is None:
                start = index
            last = index
        elif start is not None and index - last > max_gap:
            if last - start >= min_items:
                runs.append(_to_ms(start, last))
            start = None
    if start is not None and last - start >= min_items:
        runs.append(_to_ms(start, last))
    return runs


def _close(a: int, b: int) -> bool:
    return ((a ^ b) & 0xFFFFFFFF).bit_count() <= _MAX_BIT_ERRORS


def _to_ms(first: int, last: int) -> tuple[int, int]:
    return round(first * ITEM_MS), round((last + 1) * ITEM_MS)


def _merged(zones: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(zones):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
