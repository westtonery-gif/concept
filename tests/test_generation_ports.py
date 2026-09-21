"""Tests for the two generation ports' value types (GENERATION_ACCEPTANCE §2, GNP; ADR-0066)."""

from __future__ import annotations

import pytest

from omemo_content_factory.adapters.image_generator import GeneratedImage, ImageGenerationRequest
from omemo_content_factory.adapters.video_generator import (
    GeneratedVideo,
    VideoGenerationRequest,
    VideoJob,
    VideoJobResult,
    VideoJobState,
)

VIDEO = GeneratedVideo(path="/v.mp4", duration_ms=5000, width=720, height=1280, container="mp4")


def test_gnp_01_requests_refuse_blank_fields() -> None:
    with pytest.raises(ValueError):
        ImageGenerationRequest(reference=" ", prompt="p", destination="/d.png")
    with pytest.raises(ValueError):
        ImageGenerationRequest(reference="/r.jpg", prompt="", destination="/d.png")
    with pytest.raises(ValueError):
        VideoGenerationRequest(first_frame="/a", last_frame="", prompt="p", duration_s=5)


@pytest.mark.parametrize("duration", [0, -1, True, 2.5])
def test_gnp_02_a_video_request_needs_a_positive_whole_duration(duration: object) -> None:
    with pytest.raises(ValueError):
        VideoGenerationRequest(
            first_frame="/a",
            last_frame="/b",
            prompt="p",
            duration_s=duration,  # type: ignore[arg-type]
        )


def test_gnp_03_measurements_must_be_positive() -> None:
    with pytest.raises(ValueError):
        GeneratedImage(path="/i.png", width=0, height=10, media_type="image/png")
    with pytest.raises(ValueError):
        GeneratedVideo(path="/v.mp4", duration_ms=0, width=720, height=1280, container="mp4")


def test_gnp_04_a_result_carries_a_video_exactly_when_completed() -> None:
    assert VideoJobResult(state=VideoJobState.COMPLETED, video=VIDEO).video is VIDEO
    assert VideoJobResult(state=VideoJobState.PENDING).video is None
    with pytest.raises(ValueError):
        VideoJobResult(state=VideoJobState.COMPLETED)
    for state in (VideoJobState.PENDING, VideoJobState.FAILED, VideoJobState.REJECTED):
        with pytest.raises(ValueError):
            VideoJobResult(state=state, video=VIDEO)


def test_gnp_05_a_job_needs_an_id() -> None:
    with pytest.raises(ValueError):
        VideoJob(job_id="  ")
