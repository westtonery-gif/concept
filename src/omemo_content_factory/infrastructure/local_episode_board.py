"""One explicitly supplied local episode, without a Notion connection."""

from omemo_content_factory.adapters.episode_board import IncomingEpisode
from omemo_content_factory.domain.run import RunStatus


class LocalEpisodeBoard:
    def __init__(self, episode: IncomingEpisode) -> None:
        self.episode = episode

    def fetch_episode(self, episode_ref: str, /) -> IncomingEpisode | None:
        return self.episode if episode_ref == self.episode.episode_ref else None

    def report_status(self, episode_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        print(f"{episode_ref}: {status.value} (run {run_id})", flush=True)
