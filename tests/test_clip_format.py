"""Tests for the deterministic clip format check (ADR-0056 §1, ADR-0058).

Maps CLIPPING_ACCEPTANCE.md §5 (RND-01…04). RND-05 — that a violation becomes a ``FAILED`` Task
with a stable reason rather than a ``flagged`` verdict — is an orchestration property and is
covered with the department's production path (`CRN`), not here.
"""

from __future__ import annotations

import pytest

from omemo_content_factory.adapters.clip_renderer import RenderedClip
from omemo_content_factory.application.clip_format import ClipFormatLimits, check_clip_format

LIMITS = ClipFormatLimits(max_duration_ms=180_000, containers=("mp4",))


def _clip(**overrides: object) -> RenderedClip:
    fields: dict[str, object] = {
        "path": "/clips/episode-1-01.mp4",
        "duration_ms": 120_000,
        "width": 1920,
        "height": 1080,
        "container": "mp4",
    }
    fields.update(overrides)
    return RenderedClip(**fields)  # type: ignore[arg-type]


def test_rnd_01_a_clip_within_the_limits_has_no_violations() -> None:
    assert check_clip_format(_clip(), limits=LIMITS) == ()


def test_rnd_01_the_boundary_value_is_inside_the_limit() -> None:
    assert check_clip_format(_clip(duration_ms=180_000), limits=LIMITS) == ()


def test_rnd_02_a_clip_over_the_duration_limit_is_reported_with_its_numbers() -> None:
    violations = check_clip_format(_clip(duration_ms=180_001), limits=LIMITS)
    assert len(violations) == 1
    assert "180001" in violations[0] and "180000" in violations[0]


def test_rnd_03_a_container_outside_the_accepted_list_is_reported() -> None:
    violations = check_clip_format(_clip(container="mkv"), limits=LIMITS)
    assert len(violations) == 1
    assert "mkv" in violations[0]


def test_rnd_03_the_container_check_ignores_case_and_padding() -> None:
    assert check_clip_format(_clip(container=" MP4 "), limits=LIMITS) == ()


def test_rnd_04_aspect_ratio_is_not_a_violation() -> None:
    """v1 does not reframe: the clip keeps the source's shape and the platform letterboxes it."""
    assert check_clip_format(_clip(width=1920, height=1080), limits=LIMITS) == ()
    assert check_clip_format(_clip(width=1080, height=1920), limits=LIMITS) == ()
    assert check_clip_format(_clip(width=640, height=640), limits=LIMITS) == ()


def test_rnd_02_several_violations_are_all_reported() -> None:
    violations = check_clip_format(_clip(duration_ms=400_000, container="mov"), limits=LIMITS)
    assert len(violations) == 2


@pytest.mark.parametrize(
    "overrides",
    [{"max_duration_ms": 0}, {"max_duration_ms": -1}, {"containers": ()}, {"containers": ("",)}],
)
def test_rnd_01_ill_formed_limits_are_refused(overrides: dict[str, object]) -> None:
    fields: dict[str, object] = {"max_duration_ms": 1_000, "containers": ("mp4",)}
    fields.update(overrides)
    with pytest.raises(ValueError):
        ClipFormatLimits(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [{"duration_ms": 0}, {"width": 0}, {"height": -1}, {"path": " "}, {"container": ""}],
)
def test_rnd_01_an_ill_formed_measurement_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _clip(**overrides)
