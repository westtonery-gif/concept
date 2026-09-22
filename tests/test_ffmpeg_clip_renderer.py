"""Tests for the real ``ClipRenderer`` and the filesystem ``EpisodeSource`` (ADR-0063/0071).

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
    FinishRequest,
)
from omemo_content_factory.adapters.episode_source import (
    EpisodeSource,
    EpisodeSourceError,
    LocatedEpisode,
)
from omemo_content_factory.infrastructure.ffmpeg_clip_renderer import (
    FfmpegClipRenderer,
    _drawable,
)
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


needs_libass = pytest.mark.skipif(
    shutil.which("ffmpeg") is None
    or "libass"
    not in subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", "filter=subtitles"], capture_output=True, text=True
    ).stdout,
    reason="this ffmpeg has no libass subtitles filter (ADR-0071)",
)


def _black_episode(tmp_path: Path) -> Path:
    """Three seconds of black: any light pixel in a frame of the render is a drawn caption."""
    path = tmp_path / "black.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:duration=3:size=640x360:rate=25",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=duration=3",
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
    return path


def _light_pixels(video: Path, at_seconds: float) -> int:
    """How many pixels of the frame at ``at_seconds`` are clearly not black."""
    frame = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            f"{at_seconds}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    assert frame, "a frame was extracted"
    return sum(1 for value in frame if value > 128)


@needs_libass
def test_fcr_02_captions_are_burnt_in_while_they_are_said(tmp_path: Path) -> None:
    """ADR-0071: the caption is drawn during its own interval and not outside it."""
    black = _black_episode(tmp_path)
    captions = (Caption(start_ms=0, end_ms=1_000, text="Морти, это видно?"),)
    clip = FfmpegClipRenderer().render(
        _request(black, tmp_path / "captioned.mp4", start_ms=0, end_ms=3_000, captions=captions)
    )
    assert _light_pixels(Path(clip.path), 0.5) > 200, "the caption is on screen while it is said"
    assert _light_pixels(Path(clip.path), 2.0) == 0, "and gone once it is over"


@needs_libass
def test_fcr_02_a_clip_without_captions_is_left_clean(tmp_path: Path) -> None:
    black = _black_episode(tmp_path)
    clip = FfmpegClipRenderer().render(
        _request(black, tmp_path / "clean.mp4", start_ms=0, end_ms=3_000)
    )
    assert _light_pixels(Path(clip.path), 0.5) == 0


@needs_libass
def test_fcr_02_caption_text_cannot_inject_ass_markup(tmp_path: Path) -> None:
    """Override braces would hide the text; a backslash could start an escape (ADR-0071 §1)."""
    black = _black_episode(tmp_path)
    captions = (Caption(start_ms=0, end_ms=1_000, text="{\\alpha&HFF&}невидимка\\N"),)
    clip = FfmpegClipRenderer().render(
        _request(black, tmp_path / "injected.mp4", start_ms=0, end_ms=3_000, captions=captions)
    )
    assert _light_pixels(Path(clip.path), 0.5) > 200, "the markup was drawn as text, not obeyed"


@needs_ffmpeg
def test_fcr_09_a_canvas_keeps_the_picture_whole_between_black_bars(tmp_path: Path) -> None:
    """ADR-0075: 16:9 on 9:16, uncropped and centred; the bars are ours, black, for banners."""
    source = tmp_path / "white.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:duration=2:size=320x180:rate=10",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    clip = FfmpegClipRenderer(canvas=(180, 320)).render(
        _request(source, tmp_path / "vertical.mp4", start_ms=0, end_ms=2_000)
    )
    assert (clip.width, clip.height) == (180, 320)
    frame = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
            "-i",
            clip.path,
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    rows = [frame[row * 180 : (row + 1) * 180] for row in range(320)]
    lit = [row for row in range(320) if sum(rows[row]) / 180 > 128]
    picture = max(lit) - min(lit) + 1
    assert abs(picture - 101) <= 4, "180 wide at 16:9 is about 101 rows of picture"
    assert abs(min(lit) - (320 - picture) / 2) <= 3, "centred"
    assert all(value < 32 for value in rows[5] + rows[314]), "black above and below"


def test_fcr_09_a_canvas_needs_two_positive_even_sides() -> None:
    for bad in ((0, 1920), (1081, 1920), (1080,)):
        with pytest.raises(ValueError, match="canvas"):
            FfmpegClipRenderer(canvas=bad)  # type: ignore[arg-type]


@needs_libass
def test_fcr_10_the_headline_and_footer_go_into_the_bars(tmp_path: Path) -> None:
    """ADR-0079: headline and footer in the bottom bar; the top bar stays empty for banners."""
    black = _black_episode(tmp_path)
    renderer = FfmpegClipRenderer(canvas=(360, 640))
    clip = renderer.render(_request(black, tmp_path / "clip.mp4", start_ms=0, end_ms=2_000))
    framed = renderer.finish(
        FinishRequest(
            source_path=clip.path,
            headline="Рик и Морти 😂 в полях",
            footer="@concept",
            destination=str(tmp_path / "clip-post.mp4"),
        )
    )
    assert (framed.width, framed.height) == (360, 640)
    frame = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            "0.5",
            "-i",
            framed.path,
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    rows = [frame[row * 360 : (row + 1) * 360] for row in range(640)]
    lit = [row for row in range(640) if any(value > 100 for value in rows[row])]
    bar = (640 - 360 * 9 // 16) // 2
    assert not any(row < bar for row in lit), "the top bar is kept for banners"
    assert any(row > 640 - bar for row in lit), "the headline and footer are in the bottom bar"
    assert not any(bar + 5 < row < 640 - bar - 5 for row in lit), "the picture band is left alone"


def test_fcr_10_a_render_without_a_canvas_has_no_bars_to_frame(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"0")
    with pytest.raises(ClipRendererError, match="canvas"):
        FfmpegClipRenderer().finish(
            FinishRequest(source_path=str(source), headline="x", footer=None, destination="out.mp4")
        )


def test_fcr_10_emoji_are_dropped_from_burnt_text() -> None:
    assert _drawable("Рик сделал пса умным 🐶 ❤️ ok") == "Рик сделал пса умным ok"


def test_fcr_08_an_ffmpeg_without_libass_is_refused_by_name(
    episode_stub_dir: Path, tmp_path: Path
) -> None:
    fake = episode_stub_dir / "ffmpeg"
    fake.write_text(
        "#!/bin/sh\necho \"No such filter: 'subtitles'\" >&2\nexit 8\n", encoding="utf-8"
    )
    fake.chmod(0o755)
    source = tmp_path / "episode.mp4"
    source.write_bytes(b"0")
    captions = (Caption(start_ms=0, end_ms=1_000, text="x"),)
    with pytest.raises(ClipRendererError, match="libass"):
        FfmpegClipRenderer(ffmpeg=str(fake)).render(
            _request(source, tmp_path / "out.mp4", start_ms=0, end_ms=2_000, captions=captions)
        )


@pytest.fixture
def episode_stub_dir(tmp_path: Path) -> Path:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    return stubs


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
