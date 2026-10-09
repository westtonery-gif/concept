"""The unattended post queue (ADR-0092, ``PQU``): one clip in flight, nothing posted twice."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omemo_content_factory.adapters.clip_publisher import ClipPublisherError
from omemo_content_factory.infrastructure.file_post_queue import FilePostQueue
from omemo_content_factory.infrastructure.in_memory_clipping import InMemoryClipPublisher


def _job(folder: Path, name: str, *, title: str = "Заголовок", video: str | None = None) -> None:
    clip = folder / (video or f"{name}.mp4")
    if video is None:
        clip.write_bytes(b"x")
    (folder / f"{name}.json").write_text(
        json.dumps({"video": clip.name, "title": title, "description": "Описание"}),
        encoding="utf-8",
    )


def _queue(folder: Path, publisher: InMemoryClipPublisher, *, max_new: int = 1) -> FilePostQueue:
    return FilePostQueue(folder, publisher, platforms=publisher.platforms, max_new_per_run=max_new)


def _outcomes(queue: FilePostQueue) -> list[tuple[str, str]]:
    return [(event.name, event.outcome) for event in queue.run()]


def test_pqu_01_a_pass_submits_only_as_many_as_it_may(tmp_path: Path) -> None:
    _job(tmp_path, "a")
    _job(tmp_path, "b")
    publisher = InMemoryClipPublisher(settle=False)
    assert _outcomes(_queue(tmp_path, publisher)) == [("a", "submitted"), ("b", "waiting")]
    assert publisher.submissions == ["a-publish"]


def test_pqu_02_one_clip_stays_in_flight_at_a_time(tmp_path: Path) -> None:
    _job(tmp_path, "a")
    _job(tmp_path, "b")
    publisher = InMemoryClipPublisher(settle=False)
    queue = _queue(tmp_path, publisher)
    queue.run()
    assert _outcomes(queue) == [("a", "pending"), ("b", "waiting")]
    assert publisher.submissions == ["a-publish"]


def test_pqu_03_a_finished_clip_is_marked_and_the_next_one_goes(tmp_path: Path) -> None:
    _job(tmp_path, "a")
    _job(tmp_path, "b")
    publisher = InMemoryClipPublisher(settle=False)
    queue = _queue(tmp_path, publisher)
    queue.run()
    publisher.finish("a-publish")
    assert _outcomes(queue) == [("a", "posted"), ("b", "submitted")]
    done = json.loads((tmp_path / "a.done.json").read_text(encoding="utf-8"))
    assert done[0]["url"] == "https://youtube.example/a-publish"
    assert publisher.submissions == ["a-publish", "b-publish"]


def test_pqu_04_a_finished_queue_never_calls_the_publisher(tmp_path: Path) -> None:
    _job(tmp_path, "a")
    publisher = InMemoryClipPublisher()
    queue = _queue(tmp_path, publisher)
    queue.run()
    queue.run()  # collects it and writes the marker
    publisher.down = True  # any further call would raise
    assert queue.run() == ()


def test_pqu_05_a_crash_after_submit_posts_nothing_twice(tmp_path: Path) -> None:
    _job(tmp_path, "a")
    publisher = InMemoryClipPublisher(settle=False)
    _queue(tmp_path, publisher).run()
    # the process died before anything else; a brand-new queue object is the next pass
    assert _outcomes(_queue(tmp_path, publisher)) == [("a", "pending")]
    assert publisher.submissions == ["a-publish"]


def test_pqu_06_an_outage_raises_marks_nothing_and_recovers(tmp_path: Path) -> None:
    _job(tmp_path, "a")
    publisher = InMemoryClipPublisher()
    queue = _queue(tmp_path, publisher)
    publisher.down = True
    with pytest.raises(ClipPublisherError):
        queue.run()
    assert not list(tmp_path.glob("*.done.json"))
    publisher.down = False
    assert _outcomes(queue) == [("a", "submitted")]


def test_pqu_07_a_failed_clip_is_marked_and_never_resubmitted(tmp_path: Path) -> None:
    _job(tmp_path, "a")
    publisher = InMemoryClipPublisher(settle=False)
    queue = _queue(tmp_path, publisher)
    queue.run()
    publisher.finish("a-publish", failed_platform="youtube")
    assert _outcomes(queue) == [("a", "failed")]
    assert (tmp_path / "a.failed.json").exists()
    assert queue.run() == ()
    assert publisher.submissions == ["a-publish"]


def test_pqu_08_a_bad_job_is_skipped_and_the_rest_still_post(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("not json", encoding="utf-8")
    _job(tmp_path, "b", video="gone.mp4")
    _job(tmp_path, "c", title="я" * 101)
    _job(tmp_path, "d")
    publisher = InMemoryClipPublisher(settle=False)
    assert _outcomes(_queue(tmp_path, publisher)) == [
        ("a", "invalid"),
        ("b", "invalid"),
        ("c", "invalid"),
        ("d", "submitted"),
    ]
