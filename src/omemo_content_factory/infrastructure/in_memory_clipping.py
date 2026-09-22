"""In-memory stubs of the clipping department's slow ports (ADR-0025's shape).

``FootageIndex`` needs a video-understanding vendor and ``ClipRenderer`` needs ffmpeg; neither is
available in this environment (``CLIPPING_ACCEPTANCE.md``, implementation-state table). These
stubs stand in for both so the department's production path can be assembled and proven now — they
are infrastructure like ``FakeLLMClient``, not test doubles: the core holds only the Protocol and
never learns which implementation it was given.

Each has a control side **outside** its Protocol: ``put`` files what the real service would have
found, and ``rendered`` reads back what the renderer was asked to produce. Where the contract is
silent they fail loudly (ADR-0025 §3) — indexing footage nobody filed is an error, not empty
footage, because empty footage is a legitimate answer that would hide a missing setup.
"""

from __future__ import annotations

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisherError,
    PlatformResult,
    PublishRequest,
    PublishState,
    PublishStatus,
)
from omemo_content_factory.adapters.clip_renderer import (
    ClipRendererError,
    ClipRenderRequest,
    RenderedClip,
)
from omemo_content_factory.adapters.episode_source import LocatedEpisode
from omemo_content_factory.adapters.footage_index import FootageIndexError, IndexedFootage

__all__ = [
    "InMemoryClipPublisher",
    "InMemoryClipRenderer",
    "InMemoryEpisodeSource",
    "InMemoryFootageIndex",
]


class InMemoryEpisodeSource:
    """An ``EpisodeSource`` whose files were filed by hand."""

    def __init__(self) -> None:
        self._located: dict[str, LocatedEpisode] = {}

    def put(self, source_ref: str, path: str) -> None:
        """File where ``source_ref`` lives (control side: whoever uploaded the episode)."""
        self._located[source_ref] = LocatedEpisode(source_ref=source_ref, path=path)

    def locate(self, source_ref: str, /) -> LocatedEpisode | None:
        """Where the episode is, or ``None`` when nothing answers to that handle."""
        return self._located.get(source_ref)


class InMemoryFootageIndex:
    """A ``FootageIndex`` returning footage filed for a path."""

    def __init__(self) -> None:
        self._footage: dict[str, IndexedFootage] = {}
        self.calls: list[str] = []
        self.fails: set[str] = set()

    def put(self, path: str, footage: IndexedFootage) -> None:
        """File what the service would have understood about the file at ``path``."""
        self._footage[path] = footage

    def fail(self, path: str) -> None:
        """Make the next indexing of ``path`` refuse, the way an outage would."""
        self.fails.add(path)

    def index(self, located: LocatedEpisode, /) -> IndexedFootage:
        """Answer with the filed footage; refuse for a path nobody filed."""
        self.calls.append(located.path)
        if located.path in self.fails:
            raise FootageIndexError(f"cannot index {located.path}")
        footage = self._footage.get(located.path)
        if footage is None:
            raise FootageIndexError(f"nothing is filed for {located.path}")
        return footage


class InMemoryClipRenderer:
    """A ``ClipRenderer`` that writes nothing and reports what it was asked for."""

    def __init__(self, *, width: int = 1920, height: int = 1080, container: str = "mp4") -> None:
        self._width = width
        self._height = height
        self._container = container
        self.requests: list[ClipRenderRequest] = []
        self.fails: set[str] = set()
        self.duration_override: int | None = None

    def fail(self, destination: str) -> None:
        """Make rendering to ``destination`` refuse."""
        self.fails.add(destination)

    def render(self, request: ClipRenderRequest, /) -> RenderedClip:
        """Report a clip of exactly the requested interval, without touching a filesystem."""
        self.requests.append(request)
        if request.destination in self.fails:
            raise ClipRendererError(f"cannot render {request.destination}")
        duration = (
            self.duration_override
            if self.duration_override is not None
            else request.end_ms - request.start_ms
        )
        return RenderedClip(
            path=request.destination,
            duration_ms=duration,
            width=self._width,
            height=self._height,
            container=self._container,
        )

    def rendered(self) -> tuple[str, ...]:
        """Destinations the renderer was asked to produce, oldest first."""
        return tuple(request.destination for request in self.requests)


class InMemoryClipPublisher:
    """A ``ClipPublisher`` that keeps submitted jobs; its control side plays the platforms.

    ``submit`` is idempotent per ``request_id`` like the real one. A job stays ``PENDING`` until
    the control side settles it with :meth:`finish`, unless ``settle`` says every job completes at
    once. ``submissions`` counts every ``submit`` call, so a test can prove nothing posted twice.
    """

    def __init__(self, *, platforms: tuple[str, ...] = ("youtube",), settle: bool = True) -> None:
        self.platforms = platforms
        self._settle = settle
        self._jobs: dict[str, PublishRequest] = {}
        self._outcomes: dict[str, PublishStatus] = {}
        self.submissions: list[str] = []
        self.down = False

    def submit(self, request: PublishRequest, /) -> None:
        if self.down:
            raise ClipPublisherError("the publisher is down")
        self.submissions.append(request.request_id)
        self._jobs.setdefault(request.request_id, request)
        if self._settle and request.request_id not in self._outcomes:
            self.finish(request.request_id)

    def status(self, request_id: str, /) -> PublishStatus:
        if self.down:
            raise ClipPublisherError("the publisher is down")
        if request_id not in self._jobs:
            return PublishStatus(state=PublishState.NOT_FOUND)
        return self._outcomes.get(request_id, PublishStatus(state=PublishState.PENDING))

    def finish(self, request_id: str, *, failed_platform: str | None = None) -> None:
        """Control side: the platforms took the job — all of them, or all but one."""
        request = self._jobs[request_id]
        results = tuple(
            PlatformResult(
                platform=name,
                success=name != failed_platform,
                url=None if name == failed_platform else f"https://{name}.example/{request_id}",
                message="rejected" if name == failed_platform else "Published",
            )
            for name in request.platforms
        )
        self._outcomes[request_id] = PublishStatus(
            state=PublishState.FAILED if failed_platform else PublishState.COMPLETED,
            results=results,
        )

    def published(self) -> tuple[PublishRequest, ...]:
        """Control side: every distinct job ever submitted, in order."""
        return tuple(self._jobs.values())
