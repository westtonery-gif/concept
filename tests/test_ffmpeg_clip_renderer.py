"""Tests for the real ``ClipRenderer`` and the filesystem ``EpisodeSource`` (ADR-0063).

The renderer's tests cut a **real** video that ffmpeg itself generates, and read the result back
with ffprobe. Nothing is mocked: if the adapter built a wrong command line, the file would not
exist or would not measure as expected. They skip where ffmpeg is absent, which is the honest
answer for a machine that cannot run them rather than a pass.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from omemo_content_factory.adapters.clip_renderer import (
    Caption,
    ClipRenderer,
    ClipRendererError,
    ClipRenderRequest,
)
from omemo_content_factory.adapters.episode_source import (
    EpisodeSource,
    EpisodeSourceError,
    LocatedEpisode,
)
from omemo_content_factory.infrastructure.ffmpeg_clip_renderer import FfmpegClipRenderer
from omemo_content_factory.infrastructure.file_system_episode_source import (
    EPISODE_ROOT_VAR,
    FileSystemEpisodeSource,
    episode_root_from_env,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are not installed on this machine",
)


@pytest.fixture(scope="module")
def episode(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A ten-second 320x240 video with sound, made by ffmpeg so the test owns its input."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    path = tmp_path_factory.mktemp("episode") / "episode.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=10:size=320x240:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    yield path


def _request(episode: Path, destination: Path, **overrides: object) -> ClipRenderRequest:
    fields: dict[str, object] = {
        "located": LocatedEpisode(source_ref="episode.mp4", path=str(episode)),
        "start_ms": 2_000,
        "end_ms": 5_000,
        "captions": (),
        "destination": str(destination),
    }
    fields.update(overrides)
    return ClipRenderRequest(**fields)  # type: ignore[arg-type]


# --- 1. Rendering a real clip -----------------------------------------------------------


@needs_ffmpeg
def test_fcr_01_a_real_clip_is_cut_and_measured(episode: Path, tmp_path: Path) -> None:
    renderer: ClipRenderer = FfmpegClipRenderer()
    destination = tmp_path / "clip.mp4"

    clip = renderer.render(_request(episode, destination))

    assert Path(clip.path).is_file()
    assert Path(clip.path).stat().st_size > 0
    assert abs(clip.duration_ms - 3_000) <= 250, "the cut is the interval that was asked for"
    assert (clip.width, clip.height) == (320, 240)
    assert clip.container == "mp4"


@needs_ffmpeg
def test_fcr_01_the_measurements_come_from_the_file_not_the_request(
    episode: Path, tmp_path: Path
) -> None:
    """Asking past the end of the episode gives a shorter clip — and it is reported as shorter."""
    renderer = FfmpegClipRenderer()
    clip = renderer.render(_request(episode, tmp_path / "tail.mp4", start_ms=8_000, end_ms=20_000))
    assert clip.duration_ms < 12_000, "a renderer that echoed the request would say 12000"
    assert abs(clip.duration_ms - 2_000) <= 250


@needs_ffmpeg
def test_fcr_02_captions_are_accepted_and_ignored(episode: Path, tmp_path: Path) -> None:
    """ADR-0063 §1: this ffmpeg cannot draw text, so captions ride along without being burnt."""
    renderer = FfmpegClipRenderer()
    captions = (Caption(start_ms=0, end_ms=2_000, text="a line nobody will see yet"),)
    clip = renderer.render(_request(episode, tmp_path / "captioned.mp4", captions=captions))
    assert Path(clip.path).is_file()
    assert abs(clip.duration_ms - 3_000) <= 250


@needs_ffmpeg
def test_fcr_03_a_missing_parent_directory_is_created(episode: Path, tmp_path: Path) -> None:
    clip = FfmpegClipRenderer().render(_request(episode, tmp_path / "out" / "deep" / "clip.mp4"))
    assert Path(clip.path).is_file()


# --- 2. Refusals ------------------------------------------------------------------------


def test_fcr_04_a_missing_episode_file_is_refused(tmp_path: Path) -> None:
    renderer = FfmpegClipRenderer()
    request = _request(tmp_path / "nowhere.mp4", tmp_path / "clip.mp4")
    with pytest.raises(ClipRendererError) as caught:
        renderer.render(request)
    assert "missing" in str(caught.value)


def test_fcr_05_a_missing_binary_is_refused_by_name(episode: Path, tmp_path: Path) -> None:
    renderer = FfmpegClipRenderer(ffmpeg="ffmpeg-that-is-not-installed")
    with pytest.raises(ClipRendererError) as caught:
        renderer.render(_request(episode if episode.is_file() else tmp_path, tmp_path / "c.mp4"))
    assert "ffmpeg-that-is-not-installed" in str(caught.value)


@needs_ffmpeg
def test_fcr_06_a_source_that_is_not_a_video_is_refused(tmp_path: Path) -> None:
    """A real failure of the real tool, surfaced as the port's error rather than a crash."""
    not_a_video = tmp_path / "episode.mp4"
    not_a_video.write_text("this is not a video", encoding="utf-8")
    with pytest.raises(ClipRendererError) as caught:
        FfmpegClipRenderer().render(_request(not_a_video, tmp_path / "clip.mp4"))
    assert "could not cut the clip" in str(caught.value)


@needs_ffmpeg
def test_fcr_07_a_timeout_is_refused(episode: Path, tmp_path: Path) -> None:
    renderer = FfmpegClipRenderer(timeout=0.001)
    with pytest.raises(ClipRendererError) as caught:
        renderer.render(_request(episode, tmp_path / "clip.mp4"))
    assert "timed out" in str(caught.value)


# --- 3. The filesystem episode source ---------------------------------------------------


def test_fse_01_a_file_in_the_root_is_located(tmp_path: Path) -> None:
    (tmp_path / "s01e01.mp4").write_bytes(b"0")
    source: EpisodeSource = FileSystemEpisodeSource(tmp_path)
    located = source.locate("s01e01.mp4")
    assert located == LocatedEpisode(source_ref="s01e01.mp4", path=str(tmp_path / "s01e01.mp4"))


@pytest.mark.parametrize(
    "source_ref",
    ["missing.mp4", "", "   ", "..", ".", "../outside.mp4", "sub/dir.mp4", "/etc/passwd"],
)
def test_fse_02_anything_that_is_not_a_plain_file_in_the_root_is_none(
    tmp_path: Path, source_ref: str
) -> None:
    """A board someone else edits must not be able to reach out of the folder it was given."""
    (tmp_path / "s01e01.mp4").write_bytes(b"0")
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"0")
    assert FileSystemEpisodeSource(tmp_path).locate(source_ref) is None


def test_fse_03_a_directory_is_not_an_episode(tmp_path: Path) -> None:
    (tmp_path / "season").mkdir()
    assert FileSystemEpisodeSource(tmp_path).locate("season") is None


def test_fse_04_the_root_is_required(tmp_path: Path) -> None:
    assert episode_root_from_env({EPISODE_ROOT_VAR: str(tmp_path)}) == tmp_path
    for environ in ({}, {EPISODE_ROOT_VAR: "   "}):
        with pytest.raises(EpisodeSourceError) as caught:
            episode_root_from_env(environ)
        assert EPISODE_ROOT_VAR in str(caught.value)
