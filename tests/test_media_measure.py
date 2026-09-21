"""Tests for ``infrastructure/media_measure`` (GENERATION_ACCEPTANCE §3, MED).

The fixtures are real, minimal files; ``MED-05`` also reads an MP4 that ffmpeg itself encoded, when
ffmpeg is on this machine, so the reader is held to a real encoder's output and not only to ours.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from omemo_content_factory.infrastructure.media_measure import (
    MediaMeasureError,
    measure_image,
    measure_video,
    sniff_image_type,
)
from tests.media_fixtures import jpeg, mp4, png, webp


@pytest.mark.parametrize(
    ("data", "media_type", "size"),
    [
        (png(9, 16), "image/png", (9, 16)),
        (jpeg(1080, 1920), "image/jpeg", (1080, 1920)),
        (webp(1280, 720), "image/webp", (1280, 720)),
    ],
)
def test_med_01_images_are_measured_from_their_headers(
    data: bytes, media_type: str, size: tuple[int, int]
) -> None:
    measured = measure_image(data)
    assert (measured.media_type, measured.width, measured.height) == (media_type, *size)
    assert sniff_image_type(data) == media_type


@pytest.mark.parametrize(
    "data",
    [b"", b"GIF89a....", png(4, 4)[:18], b"\xff\xd8\xff\xd9", b"RIFF\x00\x00\x00\x00WEBPXXXX"],
)
def test_med_02_an_unreadable_image_is_an_error_never_a_guess(data: bytes) -> None:
    with pytest.raises(MediaMeasureError):
        measure_image(data)


@pytest.mark.parametrize("version", [0, 1])
def test_med_03_an_mp4_reports_duration_and_the_video_tracks_size(version: int) -> None:
    measured = measure_video(mp4(5041, 720, 1280, version=version))
    assert (measured.container, measured.duration_ms, measured.width, measured.height) == (
        "mp4",
        5041,
        720,
        1280,
    )


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not a video at all",
        mp4(5000, 720, 1280)[:40],
        mp4(0, 720, 1280),
        mp4(5000, 0, 0),
    ],
)
def test_med_04_an_unreadable_video_is_an_error(data: bytes) -> None:
    with pytest.raises(MediaMeasureError):
        measure_video(data)


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    brew = Path("/opt/homebrew/bin/ffmpeg")
    return str(brew) if brew.exists() else None


def test_med_05_a_real_encoders_mp4_is_read(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    if ffmpeg is None:
        pytest.skip("ffmpeg is not installed on this machine")
    out = tmp_path / "real.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=360x640:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=2",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    measured = measure_video(out.read_bytes())
    assert (measured.width, measured.height) == (360, 640)
    assert 1900 <= measured.duration_ms <= 2100
