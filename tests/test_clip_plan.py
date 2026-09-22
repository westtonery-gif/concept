"""Tests for the pure clip plan (CLIPPING_SPEC §6, ADR-0058).

Maps CLIPPING_ACCEPTANCE.md §4 (CLP). Everything here is arithmetic over indexed footage: there is
no planner agent in v1, so a given episode must always yield the same plan.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from omemo_content_factory.adapters.clip_renderer import Caption
from omemo_content_factory.adapters.episode_board import ClipMode
from omemo_content_factory.adapters.footage_index import (
    IndexedFootage,
    SceneBreak,
    SkipZone,
    SpeechSpan,
)
from omemo_content_factory.application import clip_plan as clip_plan_module
from omemo_content_factory.application.clip_plan import ClipPlan, plan_clips

MINUTE = 60_000
TWO_MINUTES = 2 * MINUTE


def _plan(
    indexed: IndexedFootage,
    *,
    mode: ClipMode = ClipMode.CHUNK,
    chunk_ms: int = TWO_MINUTES,
    max_ms: int = TWO_MINUTES,
    tolerance_ms: int = 2_000,
) -> ClipPlan:
    return plan_clips(
        indexed,
        episode_ref="episode-1",
        mode=mode,
        chunk_ms=chunk_ms,
        max_ms=max_ms,
        pause_tolerance_ms=tolerance_ms,
    )


def _contiguous(plan: ClipPlan) -> None:
    assert [clip.index for clip in plan.clips] == list(range(1, len(plan.clips) + 1))
    for earlier, later in zip(plan.clips, plan.clips[1:], strict=False):
        assert earlier.end_ms <= later.start_ms, "clips must not overlap"
    for clip in plan.clips:
        assert clip.end_ms > clip.start_ms


def test_clp_01_chunk_covers_a_25_minute_episode_in_two_minute_pieces() -> None:
    plan = _plan(IndexedFootage(duration_ms=25 * MINUTE))
    _contiguous(plan)
    assert plan.mode is ClipMode.CHUNK
    assert plan.clips[0].start_ms == 0
    assert plan.clips[-1].end_ms == 25 * MINUTE
    assert len(plan.clips) == 13  # 25 ÷ 2 — the maintainer's "about 15" was this number
    assert sum(c.end_ms - c.start_ms for c in plan.clips) == 25 * MINUTE


def test_clp_02_a_boundary_inside_a_line_moves_to_the_nearer_pause() -> None:
    speech = (SpeechSpan(start_ms=TWO_MINUTES - 500, end_ms=TWO_MINUTES + 400, text="mid word"),)
    plan = _plan(IndexedFootage(duration_ms=4 * MINUTE, speech=speech), tolerance_ms=2_000)
    assert plan.clips[0].end_ms == TWO_MINUTES + 400, "nearer edge is the end of the line"
    assert plan.clips[1].start_ms == TWO_MINUTES + 400


def test_clp_03_with_no_pause_within_tolerance_the_boundary_stays() -> None:
    speech = (SpeechSpan(start_ms=MINUTE, end_ms=3 * MINUTE, text="a very long speech"),)
    plan = _plan(IndexedFootage(duration_ms=4 * MINUTE, speech=speech), tolerance_ms=1_000)
    assert plan.clips[0].end_ms == TWO_MINUTES, "the rule improves a cut, it never refuses one"


def test_clp_04_scene_merges_consecutive_short_scenes_under_the_maximum() -> None:
    scenes = (SceneBreak(30_000), SceneBreak(60_000), SceneBreak(100_000))
    indexed = IndexedFootage(duration_ms=200_000, scenes=scenes)
    plan = _plan(indexed, mode=ClipMode.SCENE, max_ms=TWO_MINUTES)
    _contiguous(plan)
    # Three short scenes merge while the running total fits; the fourth would break 120 s, so it
    # opens a new clip instead of being swallowed.
    assert [(c.start_ms, c.end_ms) for c in plan.clips] == [(0, 100_000), (100_000, 200_000)]
    assert all(c.end_ms - c.start_ms <= TWO_MINUTES for c in plan.clips)


def test_clp_05_a_scene_longer_than_the_maximum_is_split_like_a_chunk() -> None:
    indexed = IndexedFootage(duration_ms=5 * MINUTE, scenes=(SceneBreak(5 * MINUTE),))
    plan = _plan(indexed, mode=ClipMode.SCENE, chunk_ms=TWO_MINUTES, max_ms=TWO_MINUTES)
    _contiguous(plan)
    assert [(c.start_ms, c.end_ms) for c in plan.clips] == [
        (0, TWO_MINUTES),
        (TWO_MINUTES, 4 * MINUTE),
        (4 * MINUTE, 5 * MINUTE),
    ]


def test_clp_13_a_detected_cut_inside_a_spoken_line_is_not_a_scene_boundary() -> None:
    """The first real episode: 245 of 337 detected cuts fell mid-line (ADR-0069 §1)."""
    speech = (
        SpeechSpan(start_ms=20_000, end_ms=40_000, text="Я хочу сказать, что умным людям..."),
        SpeechSpan(start_ms=55_000, end_ms=58_000, text="Джерри!"),
    )
    scenes = (SceneBreak(30_000), SceneBreak(50_000))  # the first cuts through a line
    plan = _plan(
        IndexedFootage(duration_ms=90_000, scenes=scenes, speech=speech),
        mode=ClipMode.SCENE,
        max_ms=60_000,
    )
    assert [(c.start_ms, c.end_ms) for c in plan.clips] == [(0, 50_000), (50_000, 90_000)]


def test_clp_13_when_every_cut_is_mid_line_the_episode_is_one_scene_split_like_a_chunk() -> None:
    """Not CLP-06: cuts were found, none was a boundary — the episode is still planned."""
    speech = (SpeechSpan(start_ms=0, end_ms=5 * MINUTE, text="один бесконечный монолог"),)
    scenes = (SceneBreak(MINUTE), SceneBreak(3 * MINUTE))
    plan = _plan(
        IndexedFootage(duration_ms=5 * MINUTE, scenes=scenes, speech=speech), mode=ClipMode.SCENE
    )
    assert plan.clips, "a plan, not an empty one"
    _contiguous(plan)


def test_clp_14_a_scene_split_never_lands_inside_a_line() -> None:
    """With no pause inside the tolerance, SCENE moves the cut back to where the line starts."""
    speech = (
        SpeechSpan(start_ms=100_000, end_ms=130_000, text="долгая реплика через точку разреза"),
    )
    indexed = IndexedFootage(
        duration_ms=5 * MINUTE, scenes=(SceneBreak(5 * MINUTE - 1),), speech=speech
    )
    scene = _plan(indexed, mode=ClipMode.SCENE, tolerance_ms=2_000)
    assert scene.clips[0].end_ms == 100_000, "moved back to the line's start, under the maximum"
    for clip in scene.clips:
        for boundary in (clip.start_ms, clip.end_ms):
            assert not any(s.start_ms < boundary < s.end_ms for s in speech)

    chunk = _plan(indexed, mode=ClipMode.CHUNK, tolerance_ms=2_000)
    assert chunk.clips[0].end_ms == TWO_MINUTES, "CHUNK keeps CLP-03: the cut stays"


def test_clp_06_scene_mode_with_no_detected_scene_plans_nothing_and_does_not_raise() -> None:
    plan = _plan(IndexedFootage(duration_ms=25 * MINUTE), mode=ClipMode.SCENE)
    assert plan.clips == ()


def test_clp_07_an_episode_shorter_than_one_chunk_is_a_single_clip() -> None:
    plan = _plan(IndexedFootage(duration_ms=45_000))
    assert [(c.start_ms, c.end_ms) for c in plan.clips] == [(0, 45_000)]


def test_clp_08_each_clip_carries_the_speech_of_its_own_interval() -> None:
    speech = (
        SpeechSpan(start_ms=1_000, end_ms=5_000, text="first line"),
        SpeechSpan(start_ms=TWO_MINUTES + 1_000, end_ms=TWO_MINUTES + 5_000, text="second line"),
    )
    plan = _plan(IndexedFootage(duration_ms=4 * MINUTE, speech=speech))
    assert plan.clips[0].transcript == "first line"
    assert plan.clips[1].transcript == "second line"


def test_clp_08_a_clip_with_no_speech_has_an_empty_transcript() -> None:
    plan = _plan(IndexedFootage(duration_ms=4 * MINUTE))
    assert all(clip.transcript == "" for clip in plan.clips)


@pytest.mark.parametrize(
    ("chunk_ms", "max_ms", "tolerance_ms", "episode_ref"),
    [
        (0, TWO_MINUTES, 0, "e"),
        (TWO_MINUTES, 0, 0, "e"),
        (-1, 1, 0, "e"),
        (1, 1, -1, "e"),
        (1, 1, 0, " "),
    ],
)
def test_clp_09_ill_formed_parameters_are_refused(
    chunk_ms: int, max_ms: int, tolerance_ms: int, episode_ref: str
) -> None:
    with pytest.raises(ValueError):
        plan_clips(
            IndexedFootage(duration_ms=1_000),
            episode_ref=episode_ref,
            mode=ClipMode.CHUNK,
            chunk_ms=chunk_ms,
            max_ms=max_ms,
            pause_tolerance_ms=tolerance_ms,
        )


def test_clp_09_the_plan_is_pure_no_io_no_clock_no_randomness() -> None:
    """A plan that varied between runs would make an episode unreproducible (PROJECT.md §2)."""
    source = Path(clip_plan_module.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "dataclasses", "itertools", "omemo_content_factory"}
    forbidden = {"now", "today", "utcnow", "random", "open", "sleep"}
    used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    assert not (used & forbidden)

    indexed = IndexedFootage(
        duration_ms=7 * MINUTE,
        scenes=(SceneBreak(MINUTE), SceneBreak(3 * MINUTE)),
        speech=(SpeechSpan(start_ms=10, end_ms=20, text="x"),),
    )
    assert _plan(indexed, mode=ClipMode.SCENE) == _plan(indexed, mode=ClipMode.SCENE)


# --- CLP-10…12: timed captions (ADR-0062) ------------------------------------------------


def test_clp_10_captions_are_rebased_to_the_clip_not_the_episode() -> None:
    """The renderer is handed a clip; it must never need to know where that clip sat."""
    speech = (
        SpeechSpan(start_ms=TWO_MINUTES + 10_000, end_ms=TWO_MINUTES + 13_000, text="second clip"),
    )
    plan = _plan(IndexedFootage(duration_ms=4 * MINUTE, speech=speech))
    assert plan.clips[0].captions == ()
    assert plan.clips[1].captions == (Caption(start_ms=10_000, end_ms=13_000, text="second clip"),)


def test_clp_11_a_line_straddling_a_boundary_is_clipped_not_dropped() -> None:
    """Dropping it would lose dialogue at every cut — exactly where a viewer would notice."""
    speech = (SpeechSpan(start_ms=TWO_MINUTES - 2_000, end_ms=TWO_MINUTES + 3_000, text="across"),)
    plan = _plan(IndexedFootage(duration_ms=4 * MINUTE, speech=speech), tolerance_ms=0)
    first, second = plan.clips[0], plan.clips[1]
    assert first.captions == (
        Caption(start_ms=TWO_MINUTES - 2_000, end_ms=TWO_MINUTES, text="across"),
    )
    assert second.captions == (Caption(start_ms=0, end_ms=3_000, text="across"),)
    assert "across" in first.transcript and "across" in second.transcript


def test_clp_12_a_clip_with_no_speech_has_no_captions() -> None:
    plan = _plan(IndexedFootage(duration_ms=4 * MINUTE))
    assert all(clip.captions == () for clip in plan.clips)


def test_clp_10_every_caption_fits_inside_its_own_clip() -> None:
    speech = tuple(
        SpeechSpan(start_ms=offset, end_ms=offset + 4_000, text=f"line at {offset}")
        for offset in range(5_000, 6 * MINUTE, 17_000)
    )
    plan = _plan(IndexedFootage(duration_ms=6 * MINUTE, speech=speech))
    for clip in plan.clips:
        length = clip.end_ms - clip.start_ms
        for caption in clip.captions:
            assert 0 <= caption.start_ms < caption.end_ms <= length


# --- CLP-15: titles and credits are never in a clip (ADR-0074 §4) -----------------------


@pytest.mark.parametrize("mode", [ClipMode.CHUNK, ClipMode.SCENE])
def test_clp_15_no_clip_covers_a_skip_zone_and_its_edges_are_boundaries(mode: ClipMode) -> None:
    skips = (SkipZone(127_000, 159_000), SkipZone(1_295_000, 1_314_000))
    scenes = tuple(SceneBreak(at) for at in range(30_000, 1_320_000, 45_000))
    indexed = IndexedFootage(duration_ms=1_321_000, scenes=scenes, skips=skips)
    plan = _plan(indexed, mode=mode)
    _contiguous(plan)
    for clip in plan.clips:
        for zone in skips:
            assert clip.end_ms <= zone.start_ms or clip.start_ms >= zone.end_ms
    edges = {edge for clip in plan.clips for edge in (clip.start_ms, clip.end_ms)}
    assert {127_000, 159_000, 1_295_000, 1_314_000} <= edges
    assert all(c.end_ms - c.start_ms <= 2 * MINUTE + 2_000 for c in plan.clips)


def test_clp_15_a_sliver_between_zones_is_not_a_clip_but_a_post_credits_scene_is() -> None:
    """Episode 2's shape: titles at 0, end theme, then 38 s after it (ADR-0074 Context)."""
    skips = (SkipZone(0, 30_900), SkipZone(1_246_600, 1_280_200))
    indexed = IndexedFootage(duration_ms=1_318_600, skips=skips)
    plan = _plan(indexed)
    assert plan.clips[0].start_ms == 30_900, "nothing before the titles is worth a clip"
    assert (plan.clips[-1].start_ms, plan.clips[-1].end_ms) == (1_280_200, 1_318_600)

    tight = IndexedFootage(
        duration_ms=200_000, skips=(SkipZone(0, 50_000), SkipZone(53_000, 60_000))
    )
    assert all(c.start_ms >= 60_000 for c in _plan(tight).clips), "a 3 s sliver is dropped"


def test_clp_15_without_zones_the_plan_is_unchanged() -> None:
    indexed = IndexedFootage(duration_ms=6 * MINUTE, scenes=(SceneBreak(2 * MINUTE),))
    plain = _plan(indexed, mode=ClipMode.SCENE)
    assert [(c.start_ms, c.end_ms) for c in plain.clips] == [
        (0, 2 * MINUTE),
        (2 * MINUTE, 4 * MINUTE),
        (4 * MINUTE, 6 * MINUTE),
    ]
