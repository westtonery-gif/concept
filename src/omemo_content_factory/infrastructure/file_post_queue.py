"""A folder of clips to post, worked through one pass at a time (ADR-0092).

Each job is ``NAME.json`` (``video``, ``title``, ``description``). A pass asks the publisher for
the status of ``NAME-publish`` *first*, submits only what has never been submitted, keeps one clip
in flight and a configured number of new submissions per pass, and records a finished job as
``NAME.done.json`` / ``NAME.failed.json`` so it is not asked about again. The publisher's record is
the truth; the markers only stop the queue asking forever, so a crash anywhere is harmless.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisher,
    PlatformResult,
    PublishRequest,
    PublishState,
)
from omemo_content_factory.adapters.review_desk import PostDraft

__all__ = ["FilePostQueue", "QueueEvent"]

_DONE = ".done.json"
_FAILED = ".failed.json"


@dataclass(frozen=True, slots=True)
class QueueEvent:
    """What one pass did with one job. ``outcome`` is a short stable word."""

    name: str
    outcome: str  # posted | submitted | pending | waiting | failed | invalid
    detail: str = ""


class FilePostQueue:
    """Posts the clips of one folder, one pass per :meth:`run`."""

    def __init__(
        self,
        directory: str | Path,
        publisher: ClipPublisher,
        *,
        platforms: Sequence[str],
        max_new_per_run: int = 1,
    ) -> None:
        if max_new_per_run < 1:
            raise ValueError("a post queue starts at least one new post per pass")
        if not platforms:
            raise ValueError("a post queue names at least one platform")
        self._directory = Path(directory)
        self._publisher = publisher
        self._platforms = tuple(platforms)
        self._max_new = max_new_per_run

    def run(self) -> tuple[QueueEvent, ...]:
        """One pass. A ``ClipPublisherError`` propagates and nothing of this pass is marked."""
        events: list[QueueEvent] = []
        in_flight = False
        started = 0
        for path in self._jobs():
            name = path.name.removesuffix(".json")
            if (self._directory / f"{name}{_DONE}").exists() or (
                self._directory / f"{name}{_FAILED}"
            ).exists():
                continue
            try:
                request = self._request(name, path)
            except (OSError, ValueError, KeyError, TypeError) as error:
                events.append(QueueEvent(name, "invalid", str(error)))
                continue
            status = self._publisher.status(request.request_id)
            if status.state is PublishState.COMPLETED:
                self._mark(name, _DONE, status.results)
                events.append(QueueEvent(name, "posted", _urls(status.results)))
            elif status.state is PublishState.FAILED:
                self._mark(name, _FAILED, status.results)
                events.append(QueueEvent(name, "failed", _messages(status.results)))
            elif status.state is PublishState.PENDING:
                in_flight = True
                events.append(QueueEvent(name, "pending"))
            elif in_flight or started >= self._max_new:
                events.append(QueueEvent(name, "waiting"))
            else:
                self._publisher.submit(request)
                started += 1
                in_flight = True
                events.append(QueueEvent(name, "submitted"))
        return tuple(events)

    # --- internals ----------------------------------------------------------------------

    def _jobs(self) -> list[Path]:
        return sorted(
            path
            for path in self._directory.glob("*.json")
            if not path.name.endswith((_DONE, _FAILED))
        )

    def _request(self, name: str, path: Path) -> PublishRequest:
        job = json.loads(path.read_text(encoding="utf-8"))
        video = Path(str(job["video"]))
        if not video.is_absolute():
            video = self._directory / video
        if not video.is_file():
            raise ValueError(f"the video is missing: {video}")
        post = PostDraft(title=str(job["title"]), description=str(job["description"]))
        return PublishRequest(
            request_id=f"{name}-publish",
            video_path=str(video),
            post=post,
            platforms=self._platforms,
        )

    def _mark(self, name: str, suffix: str, results: Sequence[PlatformResult]) -> None:
        payload = [
            {"platform": r.platform, "success": r.success, "url": r.url, "message": r.message}
            for r in results
        ]
        target = self._directory / f"{name}{suffix}"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)


def _urls(results: Sequence[PlatformResult]) -> str:
    return " ".join(r.url for r in results if r.url)


def _messages(results: Sequence[PlatformResult]) -> str:
    return "; ".join(f"{r.platform}: {r.message}" for r in results if not r.success and r.message)
