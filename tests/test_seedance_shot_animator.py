"""Tests for ``SeedanceShotAnimator`` and the animation port (GENERATION_ACCEPTANCE §12, SDA).

ADR-0090.

The adapter's real ``urllib`` code talks over a real socket to a local server that plays Ark's task
API and the CDN its answer points at. Nothing is mocked below the adapter.
"""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from omemo_content_factory.adapters.shot_animator import (
    AnimationRequest,
    ShotAnimator,
    VideoGeneratorError,
    VideoJob,
    VideoJobState,
)
from omemo_content_factory.composition import CompositionError, build_shot_animator
from omemo_content_factory.infrastructure.seedance_shot_animator import (
    API_KEY_VAR,
    MODEL_VAR,
    RESOLUTION_VAR,
    SeedanceSettings,
    SeedanceShotAnimator,
    seedance_settings_from_env,
)
from tests.media_fixtures import jpeg, mp4

KEY = "ark-secret-key-value"
SETTINGS = SeedanceSettings(KEY, "seedance-1-0-pro-250528", "720p")


@dataclass
class FakeArk:
    video: bytes = field(default_factory=lambda: mp4(4000, 720, 1280))
    status: dict[str, Any] = field(default_factory=lambda: {"status": "running"})
    refusal: tuple[int, Any] | None = None
    drop_post: bool = False  # close the connection on a create without answering
    cut_cdn: int = 0
    requests: list[tuple[str, str, dict[str, str], Any]] = field(default_factory=list)
    base: str = ""


@pytest.fixture
def ark() -> Iterator[tuple[FakeArk, str]]:
    fake = FakeArk()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, payload: Any, ctype: str = "application/json") -> None:
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if fake.cut_cdn > 0 and self.path.startswith("/cdn/"):
                fake.cut_cdn -= 1
                self.wfile.write(raw[: len(raw) // 2])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(raw)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            fake.requests.append(
                ("POST", self.path, {k.lower(): v for k, v in self.headers.items()}, body)
            )
            if fake.drop_post:
                self.close_connection = True
                return
            if fake.refusal:
                self._reply(*fake.refusal)
            else:
                self._reply(200, {"id": "cgt-2026-abc"})

        def do_GET(self) -> None:
            fake.requests.append(("GET", self.path, {}, None))
            if self.path.startswith("/cdn/"):
                self._reply(200, fake.video, "video/mp4")
            else:
                self._reply(200, fake.status)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield fake, fake.base
    finally:
        server.shutdown()
        server.server_close()


def _animator(url: str) -> SeedanceShotAnimator:
    return SeedanceShotAnimator(SETTINGS, api_url=url, timeout=5.0, retry_delay=0.0)


def _frame(tmp_path: Path) -> str:
    path = tmp_path / "shot.jpeg"
    path.write_bytes(jpeg(1080, 1920))
    return str(path)


def _request(tmp_path: Path, **overrides: Any) -> AnimationRequest:
    values: dict[str, Any] = {
        "frame": _frame(tmp_path),
        "prompt": "The old keeper stamps his foot and the number on his hat flickers red.",
        "duration_s": 4,
    }
    values.update(overrides)
    return AnimationRequest(**values)


def test_sda_01_submit_posts_one_task_with_the_frame_inline_and_the_flags(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    request = _request(tmp_path)
    job = _animator(url).submit(request)
    assert job == VideoJob("cgt-2026-abc")
    ((verb, path, headers, body),) = fake.requests
    assert (verb, path) == ("POST", "/api/v3/contents/generations/tasks")
    assert headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "seedance-1-0-pro-250528"
    text, image = body["content"]
    assert text["type"] == "text" and text["text"].startswith(request.prompt)
    assert text["text"].endswith(
        "--resolution 720p --duration 4 --ratio 9:16 --watermark false --camerafixed false"
    )
    assert image["role"] == "first_frame"
    assert image["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(Path(request.frame).read_bytes()).decode()
    )


@pytest.mark.parametrize("seconds", [1, 13])
def test_sda_02_a_duration_outside_the_vendors_range_is_refused_before_any_request(
    tmp_path: Path, ark: tuple[FakeArk, str], seconds: int
) -> None:
    fake, url = ark
    with pytest.raises(VideoGeneratorError, match="2–12"):
        _animator(url).submit(_request(tmp_path, duration_s=seconds))
    assert fake.requests == []


def test_sda_03_a_submit_that_got_no_answer_is_never_sent_again(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    fake.drop_post = True
    with pytest.raises(VideoGeneratorError, match="NOT submitted again"):
        _animator(url).submit(_request(tmp_path))
    assert len(fake.requests) == 1


def test_sda_04_a_refusal_carries_the_vendors_words_and_never_the_key(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    fake.refusal = (400, {"error": {"message": "resource download failed"}})
    with pytest.raises(VideoGeneratorError) as raised:
        _animator(url).submit(_request(tmp_path))
    assert "HTTP 400" in str(raised.value) and "download failed" in str(raised.value)
    assert KEY not in str(raised.value)


def test_sda_05_a_missing_or_unreadable_frame_is_refused_before_any_request(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    note = tmp_path / "note.txt"
    note.write_text("not a picture")
    with pytest.raises(VideoGeneratorError, match="not an image"):
        _animator(url).submit(_request(tmp_path, frame=str(note)))
    with pytest.raises(VideoGeneratorError, match="cannot be read"):
        _animator(url).submit(_request(tmp_path, frame=str(tmp_path / "nope.jpeg")))
    assert fake.requests == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_sda_06_a_job_in_progress_is_pending_and_nothing_is_downloaded(
    tmp_path: Path, ark: tuple[FakeArk, str], status: str
) -> None:
    fake, url = ark
    fake.status = {"status": status}
    result = _animator(url).collect(VideoJob("cgt-1"), str(tmp_path / "clip.mp4"))
    assert result.state is VideoJobState.PENDING and result.video is None
    assert [r[1] for r in fake.requests] == ["/api/v3/contents/generations/tasks/cgt-1"]
    assert not (tmp_path / "clip.mp4").exists()


def test_sda_07_a_finished_job_is_downloaded_measured_and_written_atomically(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    fake.status = {"status": "succeeded", "content": {"video_url": f"{url}/cdn/clip.mp4"}}
    destination = tmp_path / "out" / "clip.mp4"
    first = _animator(url).collect(VideoJob("cgt-1"), str(destination))
    second = _animator(url).collect(VideoJob("cgt-1"), str(destination))  # repeatable
    assert first.state is VideoJobState.COMPLETED and first == second
    video = first.video
    assert video is not None
    assert (video.duration_ms, video.width, video.height, video.container) == (
        4000,
        720,
        1280,
        "mp4",
    )
    assert destination.read_bytes() == fake.video
    assert sorted(p.name for p in destination.parent.iterdir()) == ["clip.mp4"]


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ({"status": "failed"}, VideoJobState.FAILED),
        ({"status": "cancelled"}, VideoJobState.FAILED),
        (
            {"status": "failed", "error": {"code": "OutputVideoSensitiveContentDetected"}},
            VideoJobState.REJECTED,
        ),
    ],
)
def test_sda_08_a_failed_job_is_failed_and_a_content_refusal_is_rejected(
    tmp_path: Path, ark: tuple[FakeArk, str], status: dict[str, Any], state: VideoJobState
) -> None:
    fake, url = ark
    fake.status = status
    result = _animator(url).collect(VideoJob("cgt-1"), str(tmp_path / "clip.mp4"))
    assert result.state is state and not (tmp_path / "clip.mp4").exists()


@pytest.mark.parametrize(
    "status",
    [
        {"status": "mystery"},
        {"status": "succeeded"},
        {"status": "succeeded", "content": {"video_url": "ftp://x"}},
    ],
)
def test_sda_09_an_odd_answer_is_an_error(
    tmp_path: Path, ark: tuple[FakeArk, str], status: dict[str, Any]
) -> None:
    fake, url = ark
    fake.status = status
    with pytest.raises(VideoGeneratorError):
        _animator(url).collect(VideoJob("cgt-1"), str(tmp_path / "clip.mp4"))


def test_sda_10_a_download_cut_short_is_asked_for_again_and_a_non_video_is_refused(
    tmp_path: Path, ark: tuple[FakeArk, str]
) -> None:
    fake, url = ark
    fake.status = {"status": "succeeded", "content": {"video_url": f"{url}/cdn/clip.mp4"}}
    fake.cut_cdn = 2
    done = _animator(url).collect(VideoJob("cgt-1"), str(tmp_path / "a.mp4"))
    assert done.state is VideoJobState.COMPLETED
    fake.video = b"<html>not a video</html>"
    with pytest.raises(VideoGeneratorError, match="not a video"):
        _animator(url).collect(VideoJob("cgt-1"), str(tmp_path / "b.mp4"))
    assert not (tmp_path / "b.mp4").exists()


def test_sda_11_a_job_id_cannot_change_the_path(tmp_path: Path, ark: tuple[FakeArk, str]) -> None:
    fake, url = ark
    _animator(url).collect(VideoJob("../../v1/user"), str(tmp_path / "clip.mp4"))
    assert fake.requests[0][1].startswith("/api/v3/contents/generations/tasks/..%2F..%2Fv1%2Fuser")


def test_sda_12_settings_and_the_request(tmp_path: Path) -> None:
    with pytest.raises(VideoGeneratorError) as missing:
        seedance_settings_from_env({})
    for name in (API_KEY_VAR, MODEL_VAR, RESOLUTION_VAR):
        assert name in str(missing.value)
    environ = {API_KEY_VAR: KEY, MODEL_VAR: "seedance-1-0-pro-250528", RESOLUTION_VAR: "720p"}
    assert KEY not in repr(seedance_settings_from_env(environ))
    for bad in ({MODEL_VAR: "veo-3"}, {RESOLUTION_VAR: "4k"}):
        with pytest.raises(VideoGeneratorError):
            seedance_settings_from_env({**environ, **bad})
    assert isinstance(build_shot_animator(environ), SeedanceShotAnimator)
    with pytest.raises(CompositionError, match=RESOLUTION_VAR):
        build_shot_animator({k: v for k, v in environ.items() if k != RESOLUTION_VAR})
    for overrides in (
        {"frame": " "},
        {"prompt": ""},
        {"duration_s": 0},
        {"duration_s": True},
        {"ratio": "wide"},
    ):
        values: dict[str, Any] = {"frame": "/f.jpg", "prompt": "p", "duration_s": 3}
        values.update(overrides)
        with pytest.raises(ValueError):
            AnimationRequest(**values)
    animator: ShotAnimator = SeedanceShotAnimator(SETTINGS)
    assert animator is not None
