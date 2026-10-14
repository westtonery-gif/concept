"""One ``ClipPublisher`` over several, one per platform (ADR-0099 §4).

Each platform gets its own request id, ``<request_id>-<platform>``, so each child keeps its own
idempotency. ``status`` is ``NOT_FOUND`` while any platform has never seen its job — which is what
makes the caller call ``submit`` — and ``submit`` sends only those jobs. The whole is ``PENDING``
until every platform has settled, then ``FAILED`` if any failed, else ``COMPLETED``.
"""

from __future__ import annotations

from collections.abc import Mapping

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisher,
    ClipPublisherError,
    PublishRequest,
    PublishState,
    PublishStatus,
)

__all__ = ["FanOutPublisher"]


class FanOutPublisher:
    def __init__(self, publishers: Mapping[str, ClipPublisher]) -> None:
        if len(publishers) < 2:
            raise ValueError("a fan-out needs at least two platforms")
        self._publishers = dict(publishers)

    @property
    def platforms(self) -> tuple[str, ...]:
        return tuple(self._publishers)

    def submit(self, request: PublishRequest, /) -> None:
        unknown = [name for name in request.platforms if name not in self._publishers]
        if unknown:
            raise ClipPublisherError("не настроены площадки: " + ", ".join(unknown))
        for name in request.platforms:
            child = self._publishers[name]
            job = f"{request.request_id}-{name}"
            if child.status(job).state is PublishState.NOT_FOUND:
                child.submit(
                    PublishRequest(
                        request_id=job,
                        video_path=request.video_path,
                        post=request.post,
                        platforms=(name,),
                    )
                )

    def status(self, request_id: str, /) -> PublishStatus:
        statuses = [
            child.status(f"{request_id}-{name}") for name, child in self._publishers.items()
        ]
        states = {status.state for status in statuses}
        results = tuple(result for status in statuses for result in status.results)
        if PublishState.NOT_FOUND in states:
            return PublishStatus(PublishState.NOT_FOUND, results)
        if PublishState.PENDING in states:
            return PublishStatus(PublishState.PENDING, results)
        if PublishState.FAILED in states:
            return PublishStatus(PublishState.FAILED, results)
        return PublishStatus(PublishState.COMPLETED, results)
