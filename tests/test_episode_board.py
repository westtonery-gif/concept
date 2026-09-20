"""Tests for the episode board contract (ADR-0055, ADR-0058).

Maps CLIPPING_ACCEPTANCE.md §1 (EPB). The layer-boundary criterion EPB-05 is enforced by
``tests/test_adapter_contract.py`` (ADB-01/ADB-06), which scans every module of ``adapters/``; this
file only pins that the module is in that scan, so the boundary cannot be satisfied by the module
simply not existing.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

import omemo_content_factory.adapters as adapters_pkg
from omemo_content_factory.adapters.episode_board import (
    ClipMode,
    EpisodeBoard,
    EpisodeBoardError,
    IncomingEpisode,
)
from omemo_content_factory.domain.errors import DomainError
from omemo_content_factory.domain.run import RunStatus


def _episode(**overrides: Any) -> IncomingEpisode:
    fields: dict[str, Any] = {
        "episode_ref": "episode-1",
        "source_ref": "s01e01.mp4",
        "mode": ClipMode.SCENE,
    }
    fields.update(overrides)
    return IncomingEpisode(**fields)


@pytest.mark.parametrize(
    "overrides",
    [
        {"episode_ref": ""},
        {"episode_ref": "   "},
        {"episode_ref": None},
        {"source_ref": ""},
        {"source_ref": "  "},
        {"source_ref": 7},
    ],
)
def test_epb_01_a_blank_or_mistyped_reference_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError) as caught:
        _episode(**overrides)
    assert next(iter(overrides)) in str(caught.value)


@pytest.mark.parametrize("mode", ["scene", None, 1, True])
def test_epb_01_the_mode_must_be_a_clip_mode(mode: Any) -> None:
    with pytest.raises(ValueError) as caught:
        _episode(mode=mode)
    assert "ClipMode" in str(caught.value)


def test_epb_02_a_well_formed_episode_is_immutable() -> None:
    episode = _episode()
    assert (episode.episode_ref, episode.source_ref, episode.mode) == (
        "episode-1",
        "s01e01.mp4",
        ClipMode.SCENE,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        episode.episode_ref = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        episode.extra = 1  # type: ignore[attr-defined]  # __slots__ admits no new attribute


def test_epb_03_there_are_exactly_two_modes() -> None:
    """ADR-0058 §1 replaced SEMANTIC/BOTH; a stale name must not survive as a spare option."""
    assert {mode.name for mode in ClipMode} == {"CHUNK", "SCENE"}
    assert {mode.value for mode in ClipMode} == {"chunk", "scene"}


def test_epb_04_the_error_is_technical_and_the_port_is_structural() -> None:
    assert issubclass(EpisodeBoardError, Exception)
    assert not issubclass(EpisodeBoardError, DomainError)

    class _Board:
        def __init__(self) -> None:
            self.reported: list[tuple[str, str, RunStatus]] = []

        def fetch_episode(self, episode_ref: str, /) -> IncomingEpisode | None:
            return _episode() if episode_ref == "episode-1" else None

        def report_status(self, episode_ref: str, /, *, run_id: str, status: RunStatus) -> None:
            self.reported.append((episode_ref, run_id, status))

    board: EpisodeBoard = _Board()  # mypy --strict rejects this if the shape drifts
    assert board.fetch_episode("episode-1") == _episode()
    assert board.fetch_episode("unknown") is None
    board.report_status("episode-1", run_id="run-1", status=RunStatus.QUEUED)


def test_epb_05_the_module_is_part_of_the_scanned_adapter_layer() -> None:
    names = {path.stem for path in Path(adapters_pkg.__file__).parent.glob("*.py")}
    assert "episode_board" in names
