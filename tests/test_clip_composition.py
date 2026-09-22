"""Tests for the clipping department's Composition Root builders (ADR-0059, CLIPPING_SPEC).

Maps CLIPPING_ACCEPTANCE.md §9 (CMP). Build-time only: nothing here calls a model, touches a
network or runs ffmpeg — the point is that a missing or wrong setting fails **before** anything
starts, naming what is wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from omemo_content_factory.adapters.episode_source import EpisodeSourceError
from omemo_content_factory.adapters.footage_index import FootageIndexError
from omemo_content_factory.application.qa_evaluation import EvaluationResult
from omemo_content_factory.composition import (
    CHUNK_MS_VAR,
    CLIP_DESTINATION_VAR,
    CONTAINERS_VAR,
    MAX_DURATION_MS_VAR,
    MAX_MS_VAR,
    PAUSE_TOLERANCE_MS_VAR,
    CompositionError,
    build_clip_production,
    build_clip_renderer,
    build_clip_settings,
    build_episode_source,
    build_footage_index,
)
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.infrastructure.ffmpeg_clip_renderer import FfmpegClipRenderer
from omemo_content_factory.infrastructure.file_system_episode_source import (
    EPISODE_ROOT_VAR,
    FileSystemEpisodeSource,
)
from omemo_content_factory.infrastructure.local_footage_index import (
    MODEL_VAR,
    LocalFootageIndex,
)


class _Evaluator:
    evaluator_ref = "clip_qa_agent@v1"

    def evaluate(self, content: str) -> EvaluationResult:
        raise AssertionError("no model is called while building")

    def evaluate_in_context(self, content: str, context: str) -> EvaluationResult:
        raise AssertionError("no model is called while building")


def _episode_env(tmp_path: Path) -> dict[str, str]:
    model = tmp_path / "model.bin"
    model.write_bytes(b"0")
    return {
        "OMEMO_EPISODE_NOTION_TOKEN": "t",
        "OMEMO_EPISODE_NOTION_DATABASE_ID": "db",
        "OMEMO_EPISODE_NOTION_READY_PROPERTY": "Stage",
        "OMEMO_EPISODE_NOTION_READY_VALUE": "Ready to clip",
        "OMEMO_EPISODE_NOTION_SOURCE_PROPERTY": "Source",
        "OMEMO_EPISODE_NOTION_MODE_PROPERTY": "Mode",
        "OMEMO_EPISODE_NOTION_RUN_STATUS_PROPERTY": "Run status",
        "OMEMO_EPISODE_NOTION_RUN_ID_PROPERTY": "Run id",
        EPISODE_ROOT_VAR: str(tmp_path),
        MODEL_VAR: str(model),
        "OMEMO_RUN_STORE_PATH": str(tmp_path / "runs.sqlite3"),
    }


# --- 1. Settings, with documented defaults ----------------------------------------------


def test_cmp_01_the_clip_settings_have_workable_defaults() -> None:
    settings = build_clip_settings({})
    assert settings.chunk_ms == 120_000
    assert settings.max_ms == 120_000
    assert settings.pause_tolerance_ms == 2_000
    assert settings.limits.max_duration_ms == 180_000
    assert settings.limits.containers == ("mp4",)
    assert "{episode_ref}" in settings.destination_template


def test_cmp_01_every_setting_can_be_overridden() -> None:
    settings = build_clip_settings(
        {
            CHUNK_MS_VAR: "90000",
            MAX_MS_VAR: "150000",
            PAUSE_TOLERANCE_MS_VAR: "500",
            MAX_DURATION_MS_VAR: "600000",
            CONTAINERS_VAR: "mp4, mov",
            CLIP_DESTINATION_VAR: "out/{episode_ref}/{index:02d}.mp4",
        }
    )
    assert (settings.chunk_ms, settings.max_ms, settings.pause_tolerance_ms) == (
        90_000,
        150_000,
        500,
    )
    assert settings.limits.containers == ("mp4", "mov")
    assert settings.destination_template == "out/{episode_ref}/{index:02d}.mp4"


@pytest.mark.parametrize(
    "environ",
    [
        {CHUNK_MS_VAR: "0"},
        {CHUNK_MS_VAR: "-1"},
        {CHUNK_MS_VAR: "two minutes"},
        {MAX_DURATION_MS_VAR: "0"},
        {CONTAINERS_VAR: " , "},
    ],
)
def test_cmp_02_an_ill_formed_setting_fails_at_build_time(environ: dict[str, str]) -> None:
    with pytest.raises((CompositionError, ValueError)) as caught:
        build_clip_settings(environ)
    assert next(iter(environ)) in str(caught.value) or "container" in str(caught.value)


def test_cmp_02_the_defaults_are_a_deliberate_departure_from_the_property_rule() -> None:
    """Property names never default (ADR-0040); clip lengths do — one is someone else's column,
    the other is a product choice that is safe to start somewhere and tune."""
    assert build_clip_settings({}).chunk_ms > 0
    with pytest.raises(FootageIndexError):
        build_footage_index({})  # the model path has no default


# --- 2. Each builder fails closed, naming what is missing --------------------------------


def test_cmp_03_the_episode_source_needs_its_root() -> None:
    with pytest.raises(EpisodeSourceError, match=EPISODE_ROOT_VAR):
        build_episode_source({})


def test_cmp_03_the_footage_index_needs_its_model() -> None:
    with pytest.raises(FootageIndexError, match=MODEL_VAR):
        build_footage_index({})


def test_cmp_04_the_renderer_takes_no_configuration_yet(tmp_path: Path) -> None:
    assert isinstance(build_clip_renderer({}), FfmpegClipRenderer)


# --- 3. The whole path -------------------------------------------------------------------


def test_cmp_05_the_production_path_assembles_from_one_environment(tmp_path: Path) -> None:
    production = build_clip_production(_episode_env(tmp_path), evaluator=_Evaluator())
    assert production.has_desk is False, "no desk configured means reviews stay in the store"


def test_cmp_05_a_desk_is_carried_through(tmp_path: Path) -> None:
    from omemo_content_factory.infrastructure.in_memory_adapters import InMemoryReviewDesk

    production = build_clip_production(
        _episode_env(tmp_path), evaluator=_Evaluator(), desk=InMemoryReviewDesk()
    )
    assert production.has_desk is True


@pytest.mark.parametrize(
    "drop",
    [
        "OMEMO_EPISODE_NOTION_DATABASE_ID",
        "OMEMO_EPISODE_NOTION_MODE_PROPERTY",
        EPISODE_ROOT_VAR,
        MODEL_VAR,
    ],
)
def test_cmp_06_one_missing_variable_stops_the_whole_build(tmp_path: Path, drop: str) -> None:
    """Nothing half-built reaches a Run: the failure happens before any outside call."""
    environ = _episode_env(tmp_path)
    del environ[drop]
    with pytest.raises(Exception) as caught:
        build_clip_production(environ, evaluator=_Evaluator())
    assert drop in str(caught.value)


def test_cmp_06_no_model_is_called_while_building(tmp_path: Path) -> None:
    evaluator: Any = _Evaluator()
    build_clip_production(_episode_env(tmp_path), evaluator=evaluator)
    assert EvaluationStatus.PASSED is not None  # the evaluator would have raised if called


def test_cmp_07_the_built_parts_are_the_real_implementations(tmp_path: Path) -> None:
    environ = _episode_env(tmp_path)
    assert isinstance(build_episode_source(environ), FileSystemEpisodeSource)
    assert isinstance(build_footage_index(environ), LocalFootageIndex)


def test_cmp_08_the_canvas_is_vertical_by_default_and_configurable(tmp_path: Path) -> None:
    """ADR-0075: a product choice with a safe default, like the clip lengths."""
    environ = _episode_env(tmp_path)
    assert build_clip_renderer(environ)._canvas == (2160, 3840)  # type: ignore[attr-defined]
    source = build_clip_renderer({**environ, "OMEMO_CLIP_CANVAS": "source"})
    assert source._canvas is None  # type: ignore[attr-defined]
    square = build_clip_renderer({**environ, "OMEMO_CLIP_CANVAS": "1080x1080"})
    assert square._canvas == (1080, 1080)  # type: ignore[attr-defined]
    for bad in ("tall", "1080x", "1081x1920"):
        with pytest.raises(CompositionError, match="OMEMO_CLIP_CANVAS"):
            build_clip_renderer({**environ, "OMEMO_CLIP_CANVAS": bad})
