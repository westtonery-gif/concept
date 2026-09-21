"""Tests for ``HiggsfieldVideoGenerator`` (GENERATION_ACCEPTANCE §5, HVG; ADR-0066 §3, ADR-0067).

A local server plays **all three** parties the adapter talks to: Higgsfield's API (upload tickets,
generation submission, request status), the presigned storage that receives the frames, and the
CDN that serves the finished video. It records every request with its headers, so the tests can
check what reached whom — in particular that no Higgsfield credential ever reaches storage or the
CDN. Nothing leaves ``127.0.0.1``. The live call is ``test_live_generation.py``, opt-in.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from omemo_content_factory.adapters.video_generator import (
    VideoGenerationRequest,
    VideoGenerator,
    VideoGeneratorError,
    VideoJob,
    VideoJobState,
)
from omemo_content_factory.infrastructure.higgsfield_video_generator import (
    HiggsfieldVideoGenerator,
    HiggsfieldVideoSettings,
    higgsfield_video_settings_from_env,
)
from tests.media_fixtures import jpeg, mp4, png

KEY_ID = "hf-key-id-value"
KEY_SECRET = "hf-key-secret-value"
MODEL = "kling-video/v3.0/pro/image-to-video"
SETTINGS = HiggsfieldVideoSettings(key_id=KEY_ID, key_secret=KEY_SECRET, model=MODEL, sound="off")
REQUEST_ID = "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff"


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class FakeHiggsfield:
    base: str = ""
    status: dict[str, Any] = field(default_factory=lambda: {"status": "queued"})
    submit_answer: tuple[int, Any] | None = None
    video: bytes = field(default_factory=lambda: mp4(5041, 720, 1280))
    seen: list[Seen] = field(default_factory=list)
    stored: dict[str, bytes] = field(default_factory=dict)
    uploads: int = 0

    def of(self, method: str, prefix: str) -> list[Seen]:
        return [s for s in self.seen if s.method == method and s.path.startswith(prefix)]


@pytest.fixture
def higgsfield() -> Iterator[tuple[FakeHiggsfield, str]]:  # noqa: C901 — one fake per party
    fake = FakeHiggsfield()

    class Handler(BaseHTTPRequestHandler):
        def _record(self) -> bytes:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {k.lower(): v for k, v in self.headers.items()}
            fake.seen.append(Seen(self.command, self.path, headers, body))
            return body

        def _json(self, code: int, payload: Any) -> None:
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            body = json.loads(self._record() or b"{}")
            if self.path == "/files/generate-upload-url":
                fake.uploads += 1
                name = f"frame-{fake.uploads}"
                self._json(
                    200,
                    {
                        "public_url": f"{fake.base}/cdn/input/{name}",
                        "upload_url": f"{fake.base}/storage/{name}?X-Signature=abc",
                        "content_type": body["content_type"],
                        "upload_headers": {
                            "Content-Type": body["content_type"],
                            "x-amz-tagging": "retention=temporary",
                        },
                    },
                )
            elif self.path == "/" + MODEL:
                if fake.submit_answer is not None:
                    self._json(*fake.submit_answer)
                    return
                self._json(
                    200,
                    {
                        "status": "queued",
                        "request_id": REQUEST_ID,
                        "status_url": f"{fake.base}/requests/{REQUEST_ID}/status",
                        "cancel_url": f"{fake.base}/requests/{REQUEST_ID}/cancel",
                    },
                )
            else:
                self._json(404, {"detail": "Not Found"})

        def do_PUT(self) -> None:
            body = self._record()
            fake.stored[self.path.split("?")[0].rsplit("/", 1)[-1]] = body
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            self._record()
            if self.path == f"/requests/{REQUEST_ID}/status":
                self._json(200, {"request_id": REQUEST_ID, **fake.status})
            elif self.path == "/cdn/output.mp4":
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(fake.video)))
                self.end_headers()
                self.wfile.write(fake.video)
            else:
                self._json(404, {"detail": "Request not found"})

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


def _request(tmp_path: Path, *, duration_s: int = 5) -> VideoGenerationRequest:
    first = tmp_path / "bunker.jpg"
    first.write_bytes(jpeg(1080, 1920))
    last = tmp_path / "finished.png"
    last.write_bytes(png(9, 16))
    return VideoGenerationRequest(
        first_frame=str(first),
        last_frame=str(last),
        prompt="Timelapse: the bunker is built, day after day",
        duration_s=duration_s,
    )


def test_hvg_01_submit_uploads_both_frames_then_posts_one_generation(
    higgsfield: tuple[FakeHiggsfield, str], tmp_path: Path
) -> None:
    fake, url = higgsfield
    generator: VideoGenerator = HiggsfieldVideoGenerator(SETTINGS, api_url=url)

    job = generator.submit(_request(tmp_path))

    assert job == VideoJob(job_id=REQUEST_ID)
    assert fake.stored == {"frame-1": jpeg(1080, 1920), "frame-2": png(9, 16)}
    [submission] = fake.of("POST", "/" + MODEL)
    assert submission.headers["authorization"] == f"Key {KEY_ID}:{KEY_SECRET}"
    assert json.loads(submission.body) == {
        "prompt": "Timelapse: the bunker is built, day after day",
        "image_url": f"{url}/cdn/input/frame-1",
        "last_image_url": f"{url}/cdn/input/frame-2",
        "duration": 5,
        "sound": "off",
    }
    tickets = fake.of("POST", "/files/generate-upload-url")
    assert [json.loads(t.body) for t in tickets] == [
        {"content_type": "image/jpeg"},
        {"content_type": "image/png"},
    ]
    for put in fake.of("PUT", "/storage/"):
        assert "authorization" not in put.headers
        assert put.headers["x-amz-tagging"] == "retention=temporary"


@pytest.mark.parametrize("duration", [2, 16])
def test_hvg_02_a_duration_the_model_cannot_make_fails_before_any_call(
    higgsfield: tuple[FakeHiggsfield, str], tmp_path: Path, duration: int
) -> None:
    fake, url = higgsfield
    with pytest.raises(VideoGeneratorError, match="3-15 second"):
        HiggsfieldVideoGenerator(SETTINGS, api_url=url).submit(
            _request(tmp_path, duration_s=duration)
        )
    assert fake.seen == []


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ((401, {"detail": "Invalid credentials"}), "HTTP 401 \\(missing or invalid credentials\\)"),
        ((403, {"detail": "Not enough credits"}), "HTTP 403 \\(insufficient credits\\)"),
        ((200, {"status": "queued"}), "without a request id"),
        ((200, {"status": "queued", "request_id": "../../etc"}), "without a request id"),
    ],
)
def test_hvg_03_a_refused_or_malformed_submission_is_an_error_and_is_sent_once(
    higgsfield: tuple[FakeHiggsfield, str],
    tmp_path: Path,
    answer: tuple[int, Any],
    message: str,
) -> None:
    fake, url = higgsfield
    fake.submit_answer = answer
    with pytest.raises(VideoGeneratorError, match=message) as caught:
        HiggsfieldVideoGenerator(SETTINGS, api_url=url).submit(_request(tmp_path))
    assert len(fake.of("POST", "/" + MODEL)) == 1
    assert KEY_SECRET not in str(caught.value) and KEY_ID not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ({"status": "queued"}, VideoJobState.PENDING),
        ({"status": "in_progress"}, VideoJobState.PENDING),
        ({"status": "failed", "error": "Generation failed"}, VideoJobState.FAILED),
        ({"status": "canceled"}, VideoJobState.FAILED),
        ({"status": "nsfw"}, VideoJobState.REJECTED),
    ],
)
def test_hvg_04_collect_maps_every_unfinished_status_and_downloads_nothing(
    higgsfield: tuple[FakeHiggsfield, str],
    tmp_path: Path,
    status: dict[str, Any],
    state: VideoJobState,
) -> None:
    fake, url = higgsfield
    fake.status = status
    destination = tmp_path / "video.mp4"
    result = HiggsfieldVideoGenerator(SETTINGS, api_url=url).collect(
        VideoJob(job_id=REQUEST_ID), str(destination)
    )
    assert (result.state, result.video) == (state, None)
    assert not destination.exists()
    assert fake.of("GET", "/cdn/") == []


def test_hvg_05_a_completed_job_is_downloaded_measured_and_repeatable(
    higgsfield: tuple[FakeHiggsfield, str], tmp_path: Path
) -> None:
    fake, url = higgsfield
    fake.status = {"status": "completed", "video": {"url": f"{url}/cdn/output.mp4"}}
    generator = HiggsfieldVideoGenerator(SETTINGS, api_url=url)
    destination = str(tmp_path / "out" / "video.mp4")

    first = generator.collect(VideoJob(job_id=REQUEST_ID), destination)
    again = generator.collect(VideoJob(job_id=REQUEST_ID), destination)

    assert first == again
    assert first.state is VideoJobState.COMPLETED and first.video is not None
    assert (
        first.video.path,
        first.video.duration_ms,
        first.video.width,
        first.video.height,
        first.video.container,
    ) == (destination, 5041, 720, 1280, "mp4")
    assert Path(destination).read_bytes() == fake.video
    [status, _] = fake.of("GET", "/requests/")
    assert status.headers["authorization"] == f"Key {KEY_ID}:{KEY_SECRET}"
    for download in fake.of("GET", "/cdn/"):
        assert "authorization" not in download.headers
    assert list(Path(destination).parent.iterdir()) == [Path(destination)]


def test_hvg_06_a_downloaded_file_that_is_not_a_video_is_an_error_and_is_not_written(
    higgsfield: tuple[FakeHiggsfield, str], tmp_path: Path
) -> None:
    fake, url = higgsfield
    fake.status = {"status": "completed", "video": {"url": f"{url}/cdn/output.mp4"}}
    fake.video = b"<html>AccessDenied</html>"
    destination = tmp_path / "video.mp4"
    with pytest.raises(VideoGeneratorError, match="cannot be read") as caught:
        HiggsfieldVideoGenerator(SETTINGS, api_url=url).collect(
            VideoJob(job_id=REQUEST_ID), str(destination)
        )
    assert not destination.exists()
    assert "/cdn/" not in str(caught.value)


@pytest.mark.parametrize(
    "status",
    [
        {"status": "completed"},
        {"status": "completed", "video": {"url": "file:///etc/passwd"}},
        {"status": "exploded"},
    ],
)
def test_hvg_07_a_malformed_status_is_an_error(
    higgsfield: tuple[FakeHiggsfield, str], tmp_path: Path, status: dict[str, Any]
) -> None:
    fake, url = higgsfield
    fake.status = status
    with pytest.raises(VideoGeneratorError):
        HiggsfieldVideoGenerator(SETTINGS, api_url=url).collect(
            VideoJob(job_id=REQUEST_ID), str(tmp_path / "v.mp4")
        )


def test_hvg_08_a_job_id_that_is_not_a_request_id_never_reaches_a_url(
    higgsfield: tuple[FakeHiggsfield, str], tmp_path: Path
) -> None:
    fake, url = higgsfield
    with pytest.raises(VideoGeneratorError, match="not a Higgsfield request id"):
        HiggsfieldVideoGenerator(SETTINGS, api_url=url).collect(
            VideoJob(job_id="../files/generate-upload-url"), str(tmp_path / "v.mp4")
        )
    assert fake.seen == []


def test_hvg_09_an_unknown_job_is_an_error(
    higgsfield: tuple[FakeHiggsfield, str], tmp_path: Path
) -> None:
    _, url = higgsfield
    with pytest.raises(VideoGeneratorError, match="HTTP 404"):
        HiggsfieldVideoGenerator(SETTINGS, api_url=url).collect(
            VideoJob(job_id="00000000-0000-0000-0000-000000000000"), str(tmp_path / "v.mp4")
        )


ENV = {
    "OMEMO_HIGGSFIELD_API_KEY_ID": KEY_ID,
    "OMEMO_HIGGSFIELD_API_KEY_SECRET": KEY_SECRET,
    "OMEMO_HIGGSFIELD_VIDEO_MODEL": MODEL,
    "OMEMO_HIGGSFIELD_SOUND": "off",
}


def test_hvg_10_settings_are_four_required_variables_and_refuse_a_model_without_an_end_frame() -> (
    None
):
    settings = higgsfield_video_settings_from_env(ENV)
    assert (settings.model, settings.sound) == (MODEL, "off")
    assert KEY_ID not in repr(settings) and KEY_SECRET not in repr(settings)
    with pytest.raises(VideoGeneratorError) as caught:
        higgsfield_video_settings_from_env({"OMEMO_HIGGSFIELD_API_KEY_ID": KEY_ID})
    for name in (
        "OMEMO_HIGGSFIELD_API_KEY_SECRET",
        "OMEMO_HIGGSFIELD_VIDEO_MODEL",
        "OMEMO_HIGGSFIELD_SOUND",
    ):
        assert name in str(caught.value)
    with pytest.raises(VideoGeneratorError, match="takes an ending frame"):
        higgsfield_video_settings_from_env(
            {**ENV, "OMEMO_HIGGSFIELD_VIDEO_MODEL": "kling-video/v3.0-turbo/image-to-video"}
        )
    with pytest.raises(VideoGeneratorError, match="'on' or 'off'"):
        higgsfield_video_settings_from_env({**ENV, "OMEMO_HIGGSFIELD_SOUND": "loud"})
    with pytest.raises(ValueError):
        HiggsfieldVideoSettings(
            key_id=KEY_ID,
            key_secret=KEY_SECRET,
            model="kling-video/v3.0-turbo/image-to-video",
            sound="off",
        )
