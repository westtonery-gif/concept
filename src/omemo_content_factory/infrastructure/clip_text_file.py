"""Local YouTube Shorts text alongside a rendered clip (CLIPPING_SPEC)."""

from __future__ import annotations

import json
from pathlib import Path

from omemo_content_factory.adapters.review_desk import PostDraft
from omemo_content_factory.application.clip_review import latest_post
from omemo_content_factory.domain.run import Run


def write_youtube_text(video: Path, post: PostDraft) -> Path | None:
    """Create a UTF-8 sidecar; never replace an existing file or manual edits."""
    if not video.is_file():
        return None
    destination = video.with_suffix(".txt")
    text = f"YouTube Shorts\n\nНазвание:\n{post.title}\n\nОписание:\n{post.description}\n"
    try:
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(text)
    except FileExistsError:
        return None
    return destination


def export_youtube_texts(run: Run) -> tuple[Path, ...]:
    """Export already generated posts, including those in a resumed or completed Run."""
    written = []
    for artifact in run.artifacts:
        post = latest_post(run, artifact.artifact_id)
        if post is None:
            continue
        payload = json.loads(artifact.content)
        video = payload.get("path")
        if not isinstance(video, str) or not video:
            continue
        path = write_youtube_text(Path(video), post)
        if path is not None:
            written.append(path)
    return tuple(written)
