"""Tests for the upload-post ``ClipPublisher`` (ADR-0073 §2), ``UPP``.

The adapter's real ``urllib`` code talks over a real socket to a local server playing upload-post:
it parses the multipart upload, remembers jobs by ``request_id`` and answers the status route.
Nothing is mocked below the adapter and nothing leaves ``127.0.0.1``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisher,
    ClipPublisherError,
    PublishRequest,
    PublishState,
)
from omemo_content_factory.adapters.review_desk import PostDraft
from omemo_content_factory.infrastructure.upload_post_publisher import (
    API_KEY_VAR,
    PLATFORMS_VAR,
    PROFILE_VAR,
    YOUTUBE_PRIVACY_VAR,
    UploadPostPublisher,
    UploadPostSettings,
    upload_post_settings_from_env,
)

KEY = "up-secret-key-value"
SETTINGS = UploadPostSettings(
    api_key=KEY, profile="concept", platforms=("youtube", "tiktok"), youtube_privacy="unlisted"
)
POST = PostDraft(title="Рик замораживает Фрэнка 🥶", description="Коротко.\n#рикиморти #shorts")


@dataclass
class _FakeUploadPost:
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    uploads: list[dict[str, Any]] = field(default_factory=list)
    status_word: str = "completed"
    refuse: int | None = None

    def handle(self, method: str, raw_path: str, headers: Any, body: bytes) -> tuple[int, Any]:
        if headers.get("Authorization") != f"Apikey {KEY}":
            return 401, {"success": False, "message": "Invalid API key"}
        if self.refuse is not None:
            return self.refuse, {"success": False, "message": "quota exceeded"}
        parts = urlsplit(raw_path)
        if method == "POST" and parts.path == "/api/upload":
            return self._upload(headers, body)
        if method == "GET" and parts.path == "/api/uploadposts/status":
            request_id = parse_qs(parts.query)["request_id"][0]
            job = self.jobs.get(request_id)
            if job is None:
                return 404, {"success": False, "status": "not_found"}
            return 200, {
                "status": self.status_word,
                "results": [
                    {
                        "platform": name,
                        "success": True,
                        "message": "Published",
                        "url": f"https://{name}.example/{request_id}",
                    }
                    for name in job["platforms"]
                ],
            }
        return 404, {"message": "no such route"}

    def _upload(self, headers: Any, body: bytes) -> tuple[int, Any]:
        message = BytesParser(policy=HTTP).parsebytes(
            f"Content-Type: {headers['Content-Type']}\r\n\r\n".encode() + body
        )
        fields: dict[str, list[str]] = {}
        video = b""
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            raw = part.get_payload(decode=True)
            payload = raw if isinstance(raw, bytes) else b""
            if name == "video":
                video = payload
            else:
                fields.setdefault(str(name), []).append(payload.decode("utf-8"))
        upload = {"fields": fields, "video": video, "idempotency": headers.get("Idempotency-Key")}
        self.uploads.append(upload)
        request_id = fields["request_id"][0]
        self.jobs.setdefault(request_id, {"platforms": fields["platform[]"]})
        return 200, {"success": True, "request_id": request_id, "total_platforms": 1}


@pytest.fixture
def upload_post() -> Iterator[tuple[_FakeUploadPost, str]]:
    fake = _FakeUploadPost()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, answer = fake.handle(self.command, self.path, self.headers, body)
            payload = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _serve  # noqa: N815
        do_POST = _serve  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _publisher(url: str, settings: UploadPostSettings = SETTINGS) -> ClipPublisher:
    return UploadPostPublisher(settings, api_url=url, timeout=5.0)


def _request(tmp_path: Path, request_id: str = "run-1-artifact-1-publish") -> PublishRequest:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake video bytes")
    return PublishRequest(
        request_id=request_id,
        video_path=str(video),
        post=POST,
        platforms=SETTINGS.platforms,
    )


def test_upp_01_the_upload_carries_the_file_the_text_and_our_id(
    upload_post: tuple[_FakeUploadPost, str], tmp_path: Path
) -> None:
    fake, url = upload_post
    request = _request(tmp_path)
    _publisher(url).submit(request)
    (upload,) = fake.uploads
    fields = upload["fields"]
    assert fields["user"] == ["concept"]
    assert fields["platform[]"] == ["youtube", "tiktok"]
    assert fields["title"] == [POST.title]
    assert fields["youtube_description"] == [POST.description]
    assert fields["privacyStatus"] == ["unlisted"]
    assert fields["selfDeclaredMadeForKids"] == ["false"]
    assert fields["async_upload"] == ["true"]
    assert fields["request_id"] == [request.request_id]
    assert fields["tiktok_title"] == [f"{POST.title}\n\n{POST.description}"], "caption = both"
    assert "instagram_title" not in fields, "only the platforms asked for"
    assert upload["video"] == Path(request.video_path).read_bytes()
    assert upload["idempotency"] == request.request_id


def test_upp_02_status_maps_the_vendors_words(
    upload_post: tuple[_FakeUploadPost, str], tmp_path: Path
) -> None:
    fake, url = upload_post
    publisher = _publisher(url)
    request = _request(tmp_path)
    assert publisher.status(request.request_id).state is PublishState.NOT_FOUND
    publisher.submit(request)
    done = publisher.status(request.request_id)
    assert done.state is PublishState.COMPLETED
    assert [(r.platform, r.success) for r in done.results] == [("youtube", True), ("tiktok", True)]
    assert done.results[0].url == f"https://youtube.example/{request.request_id}"
    for word, state in (
        ("in_progress", PublishState.PENDING),
        ("queued", PublishState.PENDING),
        ("failed", PublishState.FAILED),
    ):
        fake.status_word = word
        assert publisher.status(request.request_id).state is state


def test_upp_03_an_unknown_state_is_refused_not_guessed(
    upload_post: tuple[_FakeUploadPost, str], tmp_path: Path
) -> None:
    fake, url = upload_post
    publisher = _publisher(url)
    publisher.submit(_request(tmp_path))
    fake.status_word = "exploded"
    with pytest.raises(ClipPublisherError, match="unknown state"):
        publisher.status("run-1-artifact-1-publish")


def test_upp_04_a_refusal_is_reported_without_the_key(
    upload_post: tuple[_FakeUploadPost, str], tmp_path: Path
) -> None:
    fake, url = upload_post
    fake.refuse = 429
    with pytest.raises(ClipPublisherError) as caught:
        _publisher(url).submit(_request(tmp_path))
    assert "429" in str(caught.value) and "quota exceeded" in str(caught.value)
    assert KEY not in str(caught.value)
    wrong = UploadPostSettings(
        api_key="wrong", profile="concept", platforms=("youtube",), youtube_privacy="public"
    )
    fake.refuse = None
    with pytest.raises(ClipPublisherError, match="401"):
        _publisher(url, wrong).status("x")


def test_upp_05_a_missing_file_is_refused_before_any_request(
    upload_post: tuple[_FakeUploadPost, str], tmp_path: Path
) -> None:
    fake, url = upload_post
    request = PublishRequest(
        request_id="r", video_path=str(tmp_path / "gone.mp4"), post=POST, platforms=("youtube",)
    )
    with pytest.raises(ClipPublisherError, match="missing"):
        _publisher(url).submit(request)
    assert fake.uploads == []


def test_upp_06_an_unreachable_service_is_refused() -> None:
    with pytest.raises(ClipPublisherError, match="could not be reached"):
        _publisher("http://127.0.0.1:9").status("x")


def test_upp_07_settings_come_from_the_environment_and_never_echo_the_key() -> None:
    env = {API_KEY_VAR: KEY, PROFILE_VAR: "concept", YOUTUBE_PRIVACY_VAR: "Unlisted"}
    settings = upload_post_settings_from_env(env)
    assert settings.platforms == ("youtube",), "YouTube by default"
    assert settings.youtube_privacy == "unlisted"
    assert KEY not in repr(settings)
    several = upload_post_settings_from_env({**env, PLATFORMS_VAR: " YouTube, tiktok "})
    assert several.platforms == ("youtube", "tiktok")
    with pytest.raises(ClipPublisherError) as caught:
        upload_post_settings_from_env({API_KEY_VAR: KEY})
    assert PROFILE_VAR in str(caught.value) and YOUTUBE_PRIVACY_VAR in str(caught.value)
    assert KEY not in str(caught.value)
    with pytest.raises(ClipPublisherError, match=YOUTUBE_PRIVACY_VAR):
        upload_post_settings_from_env({**env, YOUTUBE_PRIVACY_VAR: "friends"})


def test_upp_08_a_publish_request_is_well_formed() -> None:
    with pytest.raises(ValueError, match="platform"):
        PublishRequest(request_id="r", video_path="/c.mp4", post=POST, platforms=())
    with pytest.raises(ValueError, match="request_id"):
        PublishRequest(request_id=" ", video_path="/c.mp4", post=POST, platforms=("youtube",))
