"""Local entrypoint: no Notion, explicit per-clip approval and safe resumption."""

from __future__ import annotations

import importlib
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from omemo_content_factory.adapters.clip_publisher import PublishState, PublishStatus
from omemo_content_factory.infrastructure.youtube_publisher import YouTubeSettings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT))
    module = importlib.import_module("factory")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.post_youtube, "__file__", str(tmp_path / "post_youtube.py"))
    return module


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"clip")
    video.with_suffix(".txt").write_text("Название:\nTitle\n\nОписание:\nDescription\n")
    (tmp_path / ".env.youtube-demo").write_text(
        f"OMEMO_YOUTUBE_CHANNEL_ID=UC-demo\nOMEMO_YOUTUBE_RECEIPTS={tmp_path / 'receipts'}\n"
    )
    return video


def settings(clip: Path) -> YouTubeSettings:
    return YouTubeSettings(clip.parent / "token", "UC-demo", "public", clip.parent / "receipts")


@pytest.mark.parametrize("answers", [[""], ["no", ""], ["no", "yes"]])
def test_no_upload_without_exact_publish(
    factory: ModuleType, clip: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str]
) -> None:
    inputs = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    upload = MagicMock()
    monkeypatch.setattr(factory.post_youtube, "YouTubePublisher", upload)
    assert factory.review_and_post([clip], "demo", settings(clip)) == 0
    upload.assert_not_called()


def test_approval_uploads_only_selected_clip_with_audience(
    factory: ModuleType, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = iter(["yes", "publish"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    publisher = MagicMock()
    publisher.return_value.status.return_value = PublishStatus(PublishState.COMPLETED)
    monkeypatch.setattr(factory.post_youtube, "YouTubePublisher", publisher)
    assert factory.review_and_post([clip], "demo", settings(clip)) == 0
    assert publisher.call_args.args[0].made_for_kids is True
    publisher.return_value.submit.assert_called_once()
    assert publisher.return_value.submit.call_args.args[0].video_path == str(clip)


def test_edit_after_preview_invalidates_approval(
    factory: ModuleType, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def answer(prompt: str) -> str:
        if "publish" in prompt:
            clip.with_suffix(".txt").write_text("Название:\nChanged\n\nОписание:\nChanged\n")
            return "publish"
        return "no"

    monkeypatch.setattr("builtins.input", answer)
    publisher = MagicMock()
    monkeypatch.setattr(factory.post_youtube, "YouTubePublisher", publisher)
    assert factory.review_and_post([clip], "demo", settings(clip)) == 1
    publisher.assert_not_called()


def test_audience_changes_fingerprint(factory: ModuleType, clip: Path) -> None:
    _, first, _ = factory.post_youtube.prepare(clip, settings(clip))
    _, second, _ = factory.post_youtube.prepare(clip, replace(settings(clip), made_for_kids=True))
    assert first != second


def test_new_source_or_settings_do_not_reuse_old_run(factory: ModuleType, clip: Path) -> None:
    args = Namespace(count=3, output=None)
    env, reference, output = factory.local_environment(clip, args)
    assert factory.local_environment(clip, args)[1] == reference
    assert output.is_relative_to(clip.parent / "outputs")
    assert env["OMEMO_EPISODE_ROOT"] == str(clip.parent)
    clip.write_bytes(b"replaced source")
    assert factory.local_environment(clip, args)[1] != reference
    assert factory.local_environment(clip, Namespace(count=1, output=None))[1] != reference


def test_failed_qa_cannot_reach_publication(factory: ModuleType, clip: Path) -> None:
    from omemo_content_factory.domain.evaluation import EvaluationStatus

    run = MagicMock()
    run.artifacts = [MagicMock()]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(factory, "latest_qa", lambda *_: Namespace(status=EvaluationStatus.FAILED))
        patch.setattr(factory, "latest_post", MagicMock())
        assert factory.ready_clips(run) == []
        factory.latest_post.assert_not_called()


def test_draft_only_never_connects_or_posts(
    factory: ModuleType, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(factory, "preflight", lambda: None)
    monkeypatch.setattr(factory, "setup_model", lambda _: None)
    channel, post = MagicMock(), MagicMock()
    monkeypatch.setattr(factory, "setup_channel", channel)
    monkeypatch.setattr(factory, "review_and_post", post)
    monkeypatch.setattr(factory, "_build_evaluator", MagicMock())
    monkeypatch.setattr(factory, "_build_post_writer", MagicMock())
    monkeypatch.setattr(factory, "_build_moment_picker", MagicMock())
    production = MagicMock()
    monkeypatch.setattr(factory, "build_clip_production", production)
    monkeypatch.setattr(factory, "build_run_store", MagicMock())
    monkeypatch.setattr(factory, "_report", lambda _: None)
    monkeypatch.setattr(factory, "ready_clips", lambda _: [clip])
    assert factory.main([str(clip), "--draft-only"]) == 0
    channel.assert_not_called()
    post.assert_not_called()
    assert "posting" not in production.call_args.kwargs
    assert "desk" not in production.call_args.kwargs
