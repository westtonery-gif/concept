"""An ``EpisodeSource`` reading episodes from a directory on disk (ADR-0053 §7).

The first implementation of the port, and the one v1 uses: the maintainer drops the episode file in
a folder and the board's ``source_ref`` names it. A remote implementation would materialise a local
copy and return its path, so the renderer never learns about the network — which is why the port
answers with a path at all.

``source_ref`` is treated as a **file name inside the root**, never as a path: a reference
containing a separator or climbing out with ``..`` is refused rather than resolved, so a board
someone else can edit cannot reach outside the folder it was given.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from omemo_content_factory.adapters.episode_source import (
    EpisodeSourceError,
    LocatedEpisode,
)

__all__ = ["EPISODE_ROOT_VAR", "FileSystemEpisodeSource", "episode_root_from_env"]

EPISODE_ROOT_VAR = "OMEMO_EPISODE_ROOT"


def episode_root_from_env(environ: Mapping[str, str]) -> Path:
    """Read the required root; a missing or blank one fails closed, named."""
    raw = environ.get(EPISODE_ROOT_VAR, "").strip()
    if not raw:
        raise EpisodeSourceError(f"the episode source is not configured; set {EPISODE_ROOT_VAR}")
    return Path(raw)


class FileSystemEpisodeSource:
    """Resolves a ``source_ref`` to a file inside one directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def locate(self, source_ref: str, /) -> LocatedEpisode | None:
        """The file if it is a readable one inside the root, otherwise ``None``.

        A reference that is not a plain file name is ``None`` as well: it is not "the episode is
        missing" so much as "this board is not naming an episode", and both leave the department
        with nothing to clip, which is the same answer.
        """
        if not isinstance(source_ref, str) or not source_ref.strip():
            return None
        name = source_ref.strip()
        if name != Path(name).name or name in {".", ".."}:
            return None
        candidate = self._root / name
        if not candidate.is_file():
            return None
        return LocatedEpisode(source_ref=name, path=str(candidate))
