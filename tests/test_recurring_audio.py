"""Tests for finding shared audio — titles and credits — in raw fingerprints (ADR-0074), ``RAU``.

Synthetic fingerprints stand in for Chromaprint's: random 32-bit values, with one run copied into
another episode at a different offset (the theme), plus a few flipped bits (a re-encode).
"""

from __future__ import annotations

import random

from omemo_content_factory.infrastructure.recurring_audio import (
    ITEM_MS,
    shared_zones,
    title_zones,
)

ITEMS_PER_SECOND = 1000 / ITEM_MS


def _noise(seconds: float, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(round(seconds * ITEMS_PER_SECOND))]


def _blurred(values: list[int], seed: int) -> list[int]:
    """The same audio re-encoded: every value with a few bits flipped."""
    rng = random.Random(seed)
    return [value ^ (1 << rng.randrange(32)) ^ (1 << rng.randrange(32)) for value in values]


def _episode(parts: list[list[int]]) -> list[int]:
    return [value for part in parts for value in part]


THEME = _noise(30, seed=1)
CREDITS = _noise(20, seed=2)
CUE = _noise(12, seed=3)


def test_rau_01_a_theme_at_different_offsets_is_found_in_both() -> None:
    one = _episode([_noise(120, 10), THEME, _noise(900, 11), CREDITS, _noise(10, 12)])
    two = _episode([_blurred(THEME, 5), _noise(1000, 13), _blurred(CREDITS, 6), _noise(40, 14)])
    zones = shared_zones(one, two)
    assert len(zones) == 2
    (theme_start, theme_end), (credits_start, _) = zones
    assert abs(theme_start - 120_000) < 1_000 and abs(theme_end - 150_000) < 1_000
    assert abs(credits_start - 1_050_000) < 1_000


def test_rau_02_only_long_shared_audio_near_an_end_is_titles() -> None:
    """A recurring cue in the middle is story; a short one anywhere is too (ADR-0074 §2)."""
    one = _episode([_noise(100, 20), THEME, _noise(400, 21), CUE, _noise(400, 22), CREDITS])
    two = _episode([THEME, _noise(300, 23), CUE, _noise(600, 24), CREDITS, _noise(30, 25)])
    duration = round(len(one) * ITEM_MS)
    zones = title_zones(one, [two], duration_ms=duration)
    assert len(zones) == 2, "theme and credits; the 12 s mid-episode cue is not titles"
    assert zones[0][0] < duration * 0.25 and zones[1][0] > duration * 0.75


def test_rau_03_unrelated_episodes_share_nothing() -> None:
    assert title_zones(_noise(600, 30), [_noise(600, 31)], duration_ms=600_000) == []
    assert title_zones(_noise(600, 30), [], duration_ms=600_000) == []
    assert title_zones(_noise(600, 30), [[]], duration_ms=600_000) == []


def test_rau_04_zones_from_several_siblings_are_merged() -> None:
    one = _episode([_noise(60, 40), THEME, _noise(600, 41)])
    first = _episode([THEME[:200], _noise(500, 42)])
    second = _episode([_noise(10, 43), THEME[100:], _noise(500, 44)])
    (zone,) = title_zones(one, [first, second], duration_ms=round(len(one) * ITEM_MS))
    assert abs(zone[0] - 60_000) < 1_000 and abs(zone[1] - 90_000) < 1_000
