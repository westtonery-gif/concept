"""Direct publication: channel binding, uncertain uploads, and exact-content approval."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisherError,
    PublishRequest,
    PublishState,
)
from omemo_content_factory.adapters.review_desk import PostDraft
from omemo_content_factory.composition import build_posting
from omemo_content_factory.infrastructure.youtube_publisher import YouTubePublisher, YouTubeSettings

pytest.importorskip("googleapiclient")

ROOT = Path(__file__).resolve().parents[1]


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("post_youtube", ROOT / "post_youtube.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video data")
    path.with_suffix(".txt").write_text("YouTube Shorts\n\nНазвание:\nTitle\n\nОписание:\nText\n")
    return path


def _settings(tmp_path: Path) -> YouTubeSettings:
    return YouTubeSettings(tmp_path / "token.json", "UC-test", "private", tmp_path / "receipts")


def _service() -> MagicMock:
    service = MagicMock()
    service.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "UC-test"}]
    }
    service.videos.return_value.insert.return_value.next_chunk.return_value = (None, {"id": "abc"})
    return service


def _request(clip: Path) -> PublishRequest:
    return PublishRequest("one", str(clip), PostDraft("Title", "Text"), ("youtube",))


def test_direct_upload_and_receipt_prevent_duplicates_after_restart(clip: Path) -> None:
    settings = _settings(clip.parent)
    service = _service()
    publisher = YouTubePublisher(settings, service=service)
    request = _request(clip)
    assert publisher.status("one").state is PublishState.NOT_FOUND
    publisher.submit(request)
    YouTubePublisher(settings, service=service).submit(request)
    assert service.videos.return_value.insert.call_count == 1
    body = service.videos.return_value.insert.call_args.kwargs["body"]
    assert body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    assert body["snippet"] == {"title": "Title", "description": "Text"}
    assert publisher.status("one").results[0].url == "https://www.youtube.com/watch?v=abc"
    assert next(settings.receipts.glob("*.json")).stat().st_mode & 0o777 == 0o600


def test_wrong_channel_never_uploads(clip: Path) -> None:
    service = _service()
    service.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "wrong"}]
    }
    with pytest.raises(ClipPublisherError, match="другой канал"):
        YouTubePublisher(_settings(clip.parent), service=service).submit(_request(clip))
    service.videos.assert_not_called()


def test_timeout_blocks_reupload_and_does_not_expose_sdk_secret(clip: Path) -> None:
    service = _service()
    service.videos.return_value.insert.return_value.next_chunk.side_effect = TimeoutError("secret")
    publisher = YouTubePublisher(_settings(clip.parent), service=service)
    with pytest.raises(ClipPublisherError) as error:
        publisher.submit(_request(clip))
    assert "secret" not in str(error.value)
    with pytest.raises(ClipPublisherError, match="неизвестен"):
        publisher.submit(_request(clip))
    assert service.videos.return_value.insert.call_count == 1


def test_preview_and_outdated_approval_cannot_upload(
    clip: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()
    monkeypatch.setenv("OMEMO_YOUTUBE_CHANNEL_ID", "UC-test")
    publisher = MagicMock()
    monkeypatch.setattr(module, "YouTubePublisher", publisher)
    assert module.main([str(clip)]) == 0
    publisher.assert_not_called()
    assert "Ничего не отправлено" in capsys.readouterr().out
    _, fingerprint, _ = module.prepare(clip, _settings(clip.parent))
    assert module.main([str(clip), "--privacy", "public", "--approve", fingerprint]) == 1
    publisher.assert_not_called()


@pytest.mark.parametrize("change", ["video", "text", "channel", "privacy"])
def test_approval_changes_with_every_reviewed_input(clip: Path, change: str) -> None:
    module = _module()
    settings = _settings(clip.parent)
    request, fingerprint, _ = module.prepare(clip, settings)
    if change == "video":
        clip.write_bytes(b"different video")
    elif change == "text":
        clip.with_suffix(".txt").write_text("Название:\nEdited\n\nОписание:\nNew text\n")
    elif change == "channel":
        settings = replace(settings, channel_id="UC-other")
    else:
        settings = replace(settings, privacy="public")
    updated, new_fingerprint, _ = module.prepare(clip, settings)
    assert fingerprint != new_fingerprint
    if change in {"text", "privacy"}:
        assert updated.request_id == request.request_id


def test_composition_selects_direct_youtube_without_uploadpost_credentials() -> None:
    posting = build_posting(
        {"OMEMO_POSTING_PROVIDER": "youtube", "OMEMO_YOUTUBE_CHANNEL_ID": "UC-test"}
    )
    assert isinstance(posting.publisher, YouTubePublisher)
    assert posting.platforms == ("youtube",)


def test_second_channel_setup_preserves_default_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import google_auth_oauthlib.flow  # type: ignore[import-untyped]
    import googleapiclient.discovery  # type: ignore[import-untyped]

    from omemo_content_factory.infrastructure.dotenv_file import load_dotenv
    from omemo_content_factory.infrastructure.youtube_publisher import youtube_settings_from_env

    spec = importlib.util.spec_from_file_location(
        "configure_youtube", ROOT / "configure_youtube.py"
    )
    assert spec and spec.loader
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    default_env = tmp_path / ".env"
    default_env.write_text("OMEMO_YOUTUBE_CHANNEL_ID=UC-original\n")
    default_token = tmp_path / ".omemo/youtube-token.json"
    default_token.parent.mkdir()
    default_token.write_text('{"refresh_token":"original"}')
    before = (default_env.read_bytes(), default_token.read_bytes())
    flow = MagicMock()
    flow.run_local_server.return_value.to_json.return_value = json.dumps({"refresh_token": "new"})
    monkeypatch.setattr(
        google_auth_oauthlib.flow.InstalledAppFlow, "from_client_secrets_file", lambda *a, **k: flow
    )
    service = _service()
    service.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "UC-second", "snippet": {"title": "whtprod"}}]
    }
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: service)
    assert setup.main(["unused.json", "--handle", "@whtprod", "--profile", "whtprod"]) == 0
    assert before == (default_env.read_bytes(), default_token.read_bytes())
    env: dict[str, str] = {}
    load_dotenv(tmp_path / ".env.youtube-whtprod", env)
    settings = youtube_settings_from_env(env)
    assert settings.channel_id == "UC-second"
    assert settings.token_path.name == "youtube-whtprod-token.json"
    assert settings.token_path.stat().st_mode & 0o777 == 0o600


def test_profile_preview_uses_its_channel_and_rejects_default_approval(
    clip: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()
    monkeypatch.setattr(module, "__file__", str(clip.parent / "post_youtube.py"))
    monkeypatch.setenv("OMEMO_YOUTUBE_CHANNEL_ID", "UC-test")
    (clip.parent / ".env.youtube-whtprod").write_text("OMEMO_YOUTUBE_CHANNEL_ID=UC-second\n")
    publisher = MagicMock()
    monkeypatch.setattr(module, "YouTubePublisher", publisher)
    assert module.main([str(clip), "--profile", "whtprod"]) == 0
    assert '"channel_id": "UC-second"' in capsys.readouterr().out
    _, approval, _ = module.prepare(clip, _settings(clip.parent))
    assert module.main([str(clip), "--profile", "whtprod", "--approve", approval]) == 1
    publisher.assert_not_called()


@pytest.mark.parametrize("profile", ["missing", "../escape"])
def test_unknown_or_invalid_profile_never_falls_back_to_default_channel(
    clip: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    module = _module()
    monkeypatch.setattr(module, "__file__", str(clip.parent / "post_youtube.py"))
    monkeypatch.setenv("OMEMO_YOUTUBE_CHANNEL_ID", "UC-test")
    publisher = MagicMock()
    monkeypatch.setattr(module, "YouTubePublisher", publisher)
    assert module.main([str(clip), "--profile", profile]) == 1
    publisher.assert_not_called()
