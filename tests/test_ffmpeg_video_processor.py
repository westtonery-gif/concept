"""Real video processing and crash recovery for clipped media (ADR-0095)."""

from __future__ import annotations

import array
import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from omemo_content_factory.adapters.video_processor import (
    VideoProcessingError,
    VideoProcessingRequest,
    VideoProcessor,
)
from omemo_content_factory.infrastructure.ffmpeg_video_processor import FfmpegVideoProcessor

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe not installed"
)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    target = tmp_path / "нарезка с пробелами.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=white:size=640x360:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-metadata",
            "title=old-private-title",
            str(target),
        ],
        check=True,
        timeout=30,
    )
    return target


def _request(source: Path) -> VideoProcessingRequest:
    return VideoProcessingRequest("clip-task-1", str(source), str(source.parent / "final.mp4"))


def _probe(path: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            timeout=30,
        )
    )
    return data


def test_vpr_01_real_geometry_audio_metadata_and_hash(source: Path) -> None:
    before = source.read_bytes()
    processor: VideoProcessor = FfmpegVideoProcessor()
    output = processor.process(_request(source))
    assert source.read_bytes() == before
    assert output.source_sha256 == hashlib.sha256(before).hexdigest()
    assert output.output_sha256 != output.source_sha256
    assert (output.clip.width, output.clip.height) == (640, 360)
    assert output.clip.duration_ms == pytest.approx(3000 / 0.98, abs=40)
    info = _probe(output.clip.path)
    assert "old-private-title" not in json.dumps(info)
    assert info["format"]["tags"]["comment"].startswith("video-processing-v2:")
    video, audio = info["streams"]
    assert float(audio["duration"]) == pytest.approx(float(video["duration"]), abs=0.04)
    frame = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            output.clip.path,
            "-frames:v",
            "1",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=30,
    )
    assert frame[0] < 5 and frame[180 * 640 + 1] < 5 and frame[180 * 640 + 320] > 245
    pcm = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            output.clip.path,
            "-ss",
            "0.5",
            "-t",
            "1",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "-",
        ],
        timeout=30,
    )
    samples = array.array("h", pcm)
    crossings = sum(a < 0 <= b for a, b in pairwise(samples))
    assert crossings == pytest.approx(440, abs=3)
    report = json.loads(Path(output.report_path).read_text())
    assert report["output_sha256"] == output.output_sha256
    assert report["settings"]["speed"] == 0.98
    assert report["settings"]["scale"] == 0.94
    assert report["settings"]["version"] == 2
    assert Fraction(video["avg_frame_rate"]) == 30
    assert video["sample_aspect_ratio"] == "1:1"
    assert int(video["nb_frames"]) == pytest.approx(90 / 0.98, abs=1)
    assert report["output_frames"] == int(video["nb_frames"])
    assert frame[180 * 640 + 10] < 5 and frame[180 * 640 + 30] > 245


def test_vpr_02_restart_reuses_result_without_another_encode(source: Path) -> None:
    first = FfmpegVideoProcessor().process(_request(source))
    with patch.object(FfmpegVideoProcessor, "_render", side_effect=AssertionError("must reuse")):
        second = FfmpegVideoProcessor().process(_request(source))
    assert first == second


def test_vpr_03_crash_after_mp4_before_report_is_recoverable(source: Path) -> None:
    first = FfmpegVideoProcessor().process(_request(source))
    Path(first.report_path).unlink()
    with patch.object(FfmpegVideoProcessor, "_render", side_effect=AssertionError("must recover")):
        recovered = FfmpegVideoProcessor().process(_request(source))
    assert recovered == first
    assert Path(recovered.report_path).is_file()


def test_vpr_04_changed_settings_or_input_never_overwrite_existing_result(source: Path) -> None:
    first = FfmpegVideoProcessor().process(_request(source))
    before = Path(first.clip.path).read_bytes()
    with pytest.raises(VideoProcessingError, match="another processing request"):
        FfmpegVideoProcessor(speed=0.97).process(_request(source))
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(VideoProcessingError, match="another processing request"):
        FfmpegVideoProcessor().process(_request(source))
    assert Path(first.clip.path).read_bytes() == before


def test_vpr_05_tampered_result_is_not_reused(source: Path) -> None:
    first = FfmpegVideoProcessor().process(_request(source))
    path = Path(first.clip.path)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(VideoProcessingError, match="hash check"):
        FfmpegVideoProcessor().process(_request(source))


def test_vpr_06_processed_input_is_not_slowed_twice(source: Path) -> None:
    first = FfmpegVideoProcessor().process(_request(source))
    request = VideoProcessingRequest(
        "another-step", first.clip.path, str(source.parent / "second.mp4")
    )
    with patch.object(FfmpegVideoProcessor, "_render", side_effect=AssertionError("must reuse")):
        second = FfmpegVideoProcessor().process(request)
    assert first == second
    Path(first.report_path).unlink()
    with pytest.raises(VideoProcessingError, match="missing its report"):
        FfmpegVideoProcessor().process(request)


def test_vpr_07_concurrent_same_request_converges_to_one_result(source: Path) -> None:
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda _: FfmpegVideoProcessor().process(_request(source)), range(2))
        )
    assert results[0] == results[1]
    assert not list(source.parent.glob(".processing-*"))


def test_vpr_08_silent_rotated_video_and_hdr(source: Path) -> None:
    silent = source.parent / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-display_rotation",
            "90",
            "-i",
            str(source),
            "-an",
            "-c:v",
            "copy",
            str(silent),
        ],
        check=True,
        timeout=30,
    )
    result = FfmpegVideoProcessor().process(_request(silent))
    assert (result.clip.width, result.clip.height) == (360, 640)
    assert len(_probe(result.clip.path)["streams"]) == 1
    hdr = source.parent / "hdr.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-c:v",
            "libx264",
            "-x264-params",
            "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
            "-an",
            str(hdr),
        ],
        check=True,
        timeout=30,
    )
    with pytest.raises(VideoProcessingError, match="HDR"):
        FfmpegVideoProcessor().process(
            VideoProcessingRequest("hdr", str(hdr), str(source.parent / "hdr-out.mp4"))
        )


def test_vpr_09_missing_encoder_cannot_leave_a_final_file(source: Path) -> None:
    request = _request(source)
    with pytest.raises(VideoProcessingError, match="not installed"):
        FfmpegVideoProcessor(ffmpeg="uninstalled-encoder-for-test").process(request)
    assert not Path(request.destination).exists()
    assert not list(source.parent.glob(".processing-*"))


def test_vpr_10_fractional_cfr_packet_intervals_and_gop(tmp_path: Path) -> None:
    source = tmp_path / "fractional.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24000/1001",
            "-t",
            "4",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    output = FfmpegVideoProcessor().process(_request(source))
    video = _probe(output.clip.path)["streams"][0]
    assert Fraction(video["avg_frame_rate"]) == Fraction(24000, 1001)
    packets = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_packets",
                "-of",
                "json",
                output.clip.path,
            ],
            timeout=30,
        )
    )["packets"]
    timestamps = sorted(int(p["pts"]) for p in packets)
    assert len({b - a for a, b in pairwise(timestamps)}) == 1
    keys = sorted(timestamps.index(int(p["pts"])) for p in packets if "K" in p["flags"])
    assert len(keys) >= 2
    assert max(b - a for a, b in pairwise([*keys, len(packets)])) <= 60


def test_vpr_11_old_profile_is_not_reused_or_processed_twice(source: Path) -> None:
    first = FfmpegVideoProcessor().process(_request(source))
    report_path = Path(first.report_path)
    report = json.loads(report_path.read_text())
    report["settings"]["version"] = 1
    report_path.write_text(json.dumps(report))
    with pytest.raises(VideoProcessingError, match="different request"):
        FfmpegVideoProcessor().process(_request(source))
    with pytest.raises(VideoProcessingError, match="different settings"):
        FfmpegVideoProcessor().process(
            VideoProcessingRequest("upgrade", first.clip.path, str(source.parent / "upgrade.mp4"))
        )
    legacy = source.parent / "legacy.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            first.clip.path,
            "-c",
            "copy",
            "-metadata",
            "comment=video-processing-v1:legacy",
            str(legacy),
        ],
        check=True,
        timeout=30,
    )
    with pytest.raises(VideoProcessingError, match="missing its report"):
        FfmpegVideoProcessor().process(
            VideoProcessingRequest("legacy", str(legacy), str(source.parent / "upgrade.mp4"))
        )
    assert not (source.parent / "upgrade.mp4").exists()
