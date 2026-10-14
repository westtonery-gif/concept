"""Direct Instagram and TikTok publishers (ADR-0099): the real HTTP code against local servers.

`SOC` rows. A local ``ThreadingHTTPServer`` plays the Graph API, ``rupload.facebook.com`` and
TikTok's open API; receipts are real files. Nothing here reaches a vendor.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisherError,
    PlatformResult,
    PublishRequest,
    PublishState,
    PublishStatus,
)
from omemo_content_factory.adapters.review_desk import PostDraft
from omemo_content_factory.composition import CompositionError, build_posting
from omemo_content_factory.infrastructure import instagram_publisher as ig_module
from omemo_content_factory.infrastructure._social import receipt_path, write_receipt
from omemo_content_factory.infrastructure.fan_out_publisher import FanOutPublisher
from omemo_content_factory.infrastructure.instagram_publisher import (
    InstagramPublisher,
    InstagramSettings,
)
from omemo_content_factory.infrastructure.tiktok_publisher import (
    TikTokPublisher,
    TikTokSettings,
    chunk_plan,
)

Responder = Callable[["Call"], tuple[int, Any]]


@dataclass
class Call:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class Fake:
    url: str
    routes: dict[tuple[str, str], Responder] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    def on(self, method: str, path: str, responder: Responder | Any) -> None:
        if not callable(responder):
            value = responder

            def responder(_call: Call) -> tuple[int, Any]:
                return 200, value

        self.routes[(method, path)] = responder

    def made(self, method: str, path: str) -> list[Call]:
        return [c for c in self.calls if c.method == method and c.path.split("?")[0] == path]


@pytest.fixture
def fake() -> Iterator[Fake]:
    state: dict[str, Fake] = {}

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            server = state["fake"]
            length = int(self.headers.get("Content-Length") or 0)
            call = Call(
                self.command,
                self.path,
                {k.lower(): v for k, v in self.headers.items()},
                self.rfile.read(length),
            )
            server.calls.append(call)
            responder = server.routes.get((self.command, self.path.split("?")[0]))
            code, value = responder(call) if responder else (404, {"error": {"code": "nope"}})
            raw = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            self._serve()

        def do_POST(self) -> None:
            self._serve()

        def do_PUT(self) -> None:
            self._serve()

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["fake"] = Fake(f"http://127.0.0.1:{server.server_port}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state["fake"]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video-bytes" * 100)
    return path


def _request(clip: Path, platform: str, request_id: str = "job-1") -> PublishRequest:
    return PublishRequest(request_id, str(clip), PostDraft("Title", "Text #tag"), (platform,))


# --- Instagram ---------------------------------------------------------------------------

V = "v25.0"
IG = "1784"


def _ig(fake: Fake, tmp_path: Path, **kwargs: Any) -> InstagramPublisher:
    settings = InstagramSettings(
        token_path=tmp_path / "unused.json",
        user_id=IG,
        handle="@show",
        receipts=tmp_path / "ig",
        graph_url=fake.url,
        upload_url=fake.url,
        access_token="page-token",
    )
    return InstagramPublisher(settings, sleep=lambda _s: None, poll_seconds=0, **kwargs)


def _ig_routes(fake: Fake, statuses: list[str]) -> None:
    fake.on("POST", f"/{V}/{IG}/media", {"id": "c1", "uri": "ignored"})
    fake.on("POST", f"/ig-api-upload/{V}/c1", {"success": True, "message": "Upload successful."})
    queue = statuses  # the test may change it while the publisher waits
    fake.on(
        "GET",
        f"/{V}/c1",
        lambda _call: (200, {"status_code": queue.pop(0) if len(queue) > 1 else queue[0]}),
    )
    fake.on("POST", f"/{V}/{IG}/media_publish", {"id": "m1"})
    fake.on("GET", f"/{V}/m1", {"permalink": "https://www.instagram.com/reel/abc/"})


def test_soc_01_instagram_uploads_local_file_and_publishes_once(
    fake: Fake, tmp_path: Path, clip: Path
) -> None:
    _ig_routes(fake, ["IN_PROGRESS", "FINISHED"])
    publisher = _ig(fake, tmp_path)
    assert publisher.status("job-1").state is PublishState.NOT_FOUND
    publisher.submit(_request(clip, "instagram"))
    create = fake.made("POST", f"/{V}/{IG}/media")[0]
    assert create.headers["authorization"] == "Bearer page-token"
    assert create.json() == {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": "Title\n\nText #tag",
        "share_to_feed": True,
    }
    upload = fake.made("POST", f"/ig-api-upload/{V}/c1")[0]
    assert upload.headers["authorization"] == "OAuth page-token"
    assert upload.headers["offset"] == "0"
    assert upload.headers["file_size"] == str(clip.stat().st_size)
    assert upload.body == clip.read_bytes()
    assert fake.made("POST", f"/{V}/{IG}/media_publish")[0].json() == {"creation_id": "c1"}
    status = publisher.status("job-1")
    assert status == PublishStatus(
        PublishState.COMPLETED,
        (PlatformResult("instagram", True, "https://www.instagram.com/reel/abc/"),),
    )
    _ig(fake, tmp_path).submit(_request(clip, "instagram"))  # a restart: nothing is sent again
    assert len(fake.made("POST", f"/{V}/{IG}/media_publish")) == 1
    assert len(fake.made("POST", f"/{V}/{IG}/media")) == 1
    receipt = receipt_path(tmp_path / "ig", "instagram", "job-1")
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert "page-token" not in receipt.read_text()


def test_soc_02_instagram_still_processing_is_finished_by_a_later_status(
    fake: Fake, tmp_path: Path, clip: Path
) -> None:
    statuses = ["IN_PROGRESS"]
    _ig_routes(fake, statuses)
    publisher = _ig(fake, tmp_path, wait_seconds=0)
    publisher.submit(_request(clip, "instagram"))
    assert publisher.status("job-1").state is PublishState.PENDING
    assert not fake.made("POST", f"/{V}/{IG}/media_publish")
    statuses[0] = "FINISHED"
    assert publisher.status("job-1").state is PublishState.COMPLETED
    assert len(fake.made("POST", f"/{V}/{IG}/media_publish")) == 1


def test_soc_03_instagram_uncertain_publish_is_never_repeated(
    fake: Fake, tmp_path: Path, clip: Path
) -> None:
    _ig_routes(fake, ["FINISHED"])
    path = receipt_path(tmp_path / "ig", "instagram", "job-1")
    write_receipt(
        path, {"request_id": "job-1", "user_id": IG, "phase": "publishing", "container_id": "c1"}
    )
    publisher = _ig(fake, tmp_path)
    with pytest.raises(ClipPublisherError, match="неизвестен"):
        publisher.status("job-1")
    with pytest.raises(ClipPublisherError, match="неизвестен"):
        publisher.submit(_request(clip, "instagram"))
    assert not fake.calls


def test_soc_04_instagram_rejected_container_fails(fake: Fake, tmp_path: Path, clip: Path) -> None:
    _ig_routes(fake, ["ERROR"])
    publisher = _ig(fake, tmp_path)
    publisher.submit(_request(clip, "instagram"))
    status = publisher.status("job-1")
    assert status.state is PublishState.FAILED
    assert status.results[0].message is not None and "ERROR" in status.results[0].message
    assert not fake.made("POST", f"/{V}/{IG}/media_publish")


def test_soc_05_instagram_nothing_public_yet_starts_again(
    fake: Fake, tmp_path: Path, clip: Path
) -> None:
    _ig_routes(fake, ["FINISHED"])
    path = receipt_path(tmp_path / "ig", "instagram", "job-1")
    write_receipt(
        path, {"request_id": "job-1", "user_id": IG, "phase": "created", "container_id": "old"}
    )
    publisher = _ig(fake, tmp_path)
    assert publisher.status("job-1").state is PublishState.NOT_FOUND
    publisher.submit(_request(clip, "instagram"))
    assert publisher.status("job-1").state is PublishState.COMPLETED


def test_soc_06_instagram_refuses_another_account_and_oversized_files(
    fake: Fake, tmp_path: Path, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = receipt_path(tmp_path / "ig", "instagram", "job-1")
    write_receipt(path, {"request_id": "job-1", "user_id": "other", "phase": "published"})
    with pytest.raises(ClipPublisherError, match="другому аккаунту"):
        _ig(fake, tmp_path).status("job-1")
    monkeypatch.setattr(ig_module, "MAX_BYTES", 10)
    with pytest.raises(ClipPublisherError, match="300"):
        _ig(fake, tmp_path).submit(_request(clip, "instagram", "job-2"))
    assert not fake.calls


# --- TikTok ------------------------------------------------------------------------------


def _tt(fake: Fake, tmp_path: Path, *, mode: str = "inbox", expires_at: float = 1e12) -> Any:
    token = tmp_path / "tt-token.json"
    write_receipt(
        token,
        {
            "access_token": "tt-access",
            "refresh_token": "tt-refresh",
            "open_id": "open-1",
            "expires_at": expires_at,
        },
    )
    settings = TikTokSettings(
        client_key="key",
        client_secret="secret",
        token_path=token,
        open_id="open-1",
        handle="show",
        mode=mode,
        privacy="SELF_ONLY",
        receipts=tmp_path / "tt",
        api_url=fake.url,
    )
    return TikTokPublisher(settings, now=lambda: 1000.0)


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"data": data, "error": {"code": "ok", "message": ""}}


def _tt_routes(fake: Fake, *, route: str, status: list[dict[str, Any]]) -> None:
    fake.on("POST", route, _ok({"publish_id": "p1", "upload_url": f"{fake.url}/upload/xyz"}))
    fake.on("PUT", "/upload/xyz", {})
    fake.on("POST", "/v2/post/publish/status/fetch/", lambda _c: (200, _ok(status[0])))


def test_soc_07_tiktok_draft_is_pending_until_the_creator_posts(
    fake: Fake, tmp_path: Path, clip: Path
) -> None:
    status: list[dict[str, Any]] = [{"status": "SEND_TO_USER_INBOX"}]
    _tt_routes(fake, route="/v2/post/publish/inbox/video/init/", status=status)
    publisher = _tt(fake, tmp_path)
    assert publisher.status("job-1").state is PublishState.NOT_FOUND
    publisher.submit(_request(clip, "tiktok"))
    init = fake.made("POST", "/v2/post/publish/inbox/video/init/")[0]
    assert init.headers["authorization"] == "Bearer tt-access"
    size = clip.stat().st_size
    assert init.json() == {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        }
    }
    put = fake.made("PUT", "/upload/xyz")[0]
    assert put.headers["content-range"] == f"bytes 0-{size - 1}/{size}"
    assert put.body == clip.read_bytes()
    pending = publisher.status("job-1")
    assert pending.state is PublishState.PENDING
    assert pending.results[0].message is not None and "Входящих" in pending.results[0].message
    status[0] = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [42]}
    assert publisher.status("job-1").results == (
        PlatformResult("tiktok", True, "https://www.tiktok.com/@show/video/42"),
    )
    publisher.submit(_request(clip, "tiktok"))  # known job: nothing sent again
    assert len(fake.made("POST", "/v2/post/publish/inbox/video/init/")) == 1


def test_soc_08_tiktok_direct_checks_privacy_and_sends_the_caption(
    fake: Fake, tmp_path: Path, clip: Path
) -> None:
    route = "/v2/post/publish/video/init/"
    _tt_routes(fake, route=route, status=[{"status": "PROCESSING_UPLOAD"}])
    fake.on(
        "POST",
        "/v2/post/publish/creator_info/query/",
        _ok({"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}),
    )
    with pytest.raises(ClipPublisherError, match="SELF_ONLY"):
        _tt(fake, tmp_path, mode="direct").submit(_request(clip, "tiktok"))
    assert not fake.made("POST", route)
    fake.on(
        "POST",
        "/v2/post/publish/creator_info/query/",
        _ok({"privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"]}),
    )
    _tt(fake, tmp_path, mode="direct").submit(_request(clip, "tiktok", "job-2"))
    info = fake.made("POST", route)[0].json()["post_info"]
    assert info["title"] == "Title\n\nText #tag"
    assert info["privacy_level"] == "SELF_ONLY"


def test_soc_09_tiktok_refreshes_an_expiring_token_and_keeps_the_account(
    fake: Fake, tmp_path: Path, clip: Path
) -> None:
    _tt_routes(fake, route="/v2/post/publish/inbox/video/init/", status=[{"status": "FAILED"}])
    fake.on(
        "POST",
        "/v2/oauth/token/",
        {"access_token": "new", "refresh_token": "r2", "expires_in": 86400, "scope": "x"},
    )
    publisher = _tt(fake, tmp_path, expires_at=1100.0)  # under five minutes left
    publisher.submit(_request(clip, "tiktok"))
    form = urllib.parse.parse_qs(fake.made("POST", "/v2/oauth/token/")[0].body.decode())
    assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["tt-refresh"]
    assert fake.made("POST", "/v2/post/publish/inbox/video/init/")[0].headers["authorization"] == (
        "Bearer new"
    )
    stored = json.loads((tmp_path / "tt-token.json").read_text())
    assert stored["open_id"] == "open-1" and stored["expires_at"] == 1000.0 + 86400
    assert publisher.status("job-1").state is PublishState.FAILED


def test_soc_10_tiktok_without_publish_id_is_uncertain_and_blocks(
    fake: Fake, tmp_path: Path, clip: Path
) -> None:
    path = receipt_path(tmp_path / "tt", "tiktok", "job-1")
    write_receipt(path, {"request_id": "job-1", "open_id": "open-1", "mode": "inbox"})
    publisher = _tt(fake, tmp_path)
    with pytest.raises(ClipPublisherError, match="publish_id"):
        publisher.submit(_request(clip, "tiktok"))
    assert not fake.calls


def test_soc_11_tiktok_refusal_names_the_code(fake: Fake, tmp_path: Path, clip: Path) -> None:
    fake.on(
        "POST",
        "/v2/post/publish/inbox/video/init/",
        lambda _c: (403, {"error": {"code": "spam_risk_too_many_pending_share", "message": "5"}}),
    )
    with pytest.raises(ClipPublisherError, match="spam_risk_too_many_pending_share"):
        _tt(fake, tmp_path).submit(_request(clip, "tiktok"))


def test_soc_12_chunk_plan_follows_tiktok_rules() -> None:
    mb = 1024 * 1024
    assert chunk_plan(3 * mb) == (3 * mb, 1)
    assert chunk_plan(64 * mb) == (64 * mb, 1)
    assert chunk_plan(100 * mb) == (32 * mb, 3)  # the last piece carries 36 MB
    with pytest.raises(ClipPublisherError):
        chunk_plan(0)


# --- Fan-out and composition -------------------------------------------------------------


class _Stub:
    def __init__(self, platform: str, state: PublishState = PublishState.COMPLETED) -> None:
        self.platform = platform
        self.after = state
        self.jobs: dict[str, PublishRequest] = {}

    def submit(self, request: PublishRequest, /) -> None:
        self.jobs[request.request_id] = request

    def status(self, request_id: str, /) -> PublishStatus:
        if request_id not in self.jobs:
            return PublishStatus(PublishState.NOT_FOUND)
        return PublishStatus(
            self.after,
            (PlatformResult(self.platform, self.after is PublishState.COMPLETED, "u"),),
        )


def test_soc_13_fan_out_submits_each_platform_once_and_waits_for_all(clip: Path) -> None:
    yt, tt = _Stub("youtube"), _Stub("tiktok", PublishState.PENDING)
    fan = FanOutPublisher({"youtube": yt, "tiktok": tt})
    request = PublishRequest("a-publish", str(clip), PostDraft("T", "D"), ("youtube", "tiktok"))
    yt.submit(PublishRequest("a-publish-youtube", str(clip), PostDraft("T", "D"), ("youtube",)))
    assert fan.status("a-publish").state is PublishState.NOT_FOUND  # TikTok never saw it
    fan.submit(request)
    assert list(tt.jobs) == ["a-publish-tiktok"] and tt.jobs["a-publish-tiktok"].platforms == (
        "tiktok",
    )
    assert len(yt.jobs) == 1
    assert fan.status("a-publish").state is PublishState.PENDING
    tt.after = PublishState.COMPLETED
    done = fan.status("a-publish")
    assert done.state is PublishState.COMPLETED
    assert [r.platform for r in done.results] == ["youtube", "tiktok"]
    tt.after = PublishState.FAILED
    assert fan.status("a-publish").state is PublishState.FAILED


def test_soc_14_composition_builds_one_publisher_or_a_fan_out() -> None:
    env = {
        "OMEMO_YOUTUBE_CHANNEL_ID": "UC-test",
        "OMEMO_INSTAGRAM_USER_ID": IG,
        "OMEMO_TIKTOK_CLIENT_KEY": "k",
        "OMEMO_TIKTOK_CLIENT_SECRET": "s",
        "OMEMO_TIKTOK_OPEN_ID": "o",
    }
    single = build_posting({**env, "OMEMO_POSTING_PROVIDER": "instagram"})
    assert isinstance(single.publisher, InstagramPublisher)
    assert single.platforms == ("instagram",)
    many = build_posting({**env, "OMEMO_POSTING_PROVIDER": "youtube, instagram,tiktok"})
    assert isinstance(many.publisher, FanOutPublisher)
    assert many.platforms == ("youtube", "instagram", "tiktok")
    for wrong in ("facebook", "youtube,youtube", "upload-post,tiktok"):
        with pytest.raises(CompositionError):
            build_posting({**env, "OMEMO_POSTING_PROVIDER": wrong})
