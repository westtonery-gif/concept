"""One pass of the unattended post queue (ADR-0092).

    python demo_post_queue.py <queue folder>

Each ``NAME.json`` in the folder is a clip to post (``video``, ``title``, ``description``). The
pass collects what has finished, and starts the next clip when none is in flight, at most
``OMEMO_POSTING_MAX_NEW_PER_RUN`` of them, through the publishers ``OMEMO_POSTING_PROVIDER``
names (ADR-0100). It is what a schedule runs — see
``scripts/run_post_queue.sh`` — so nothing here asks a person anything.

"""

from __future__ import annotations

import os
import sys

from demo import safe_print

from omemo_content_factory.adapters.clip_publisher import ClipPublisherError
from omemo_content_factory.composition import (
    POSTS_PER_RUN_VAR,
    CompositionError,
    build_publisher,
)
from omemo_content_factory.infrastructure.dotenv_file import load_project_env
from omemo_content_factory.infrastructure.file_post_queue import FilePostQueue


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        safe_print(__doc__ or "")
        return 2
    try:
        publisher, platforms = build_publisher(os.environ)
        raw = os.environ.get(POSTS_PER_RUN_VAR, "").strip()
        queue = FilePostQueue(
            argv[0],
            publisher,
            platforms=platforms,
            max_new_per_run=int(raw) if raw else 1,
        )
        events = queue.run()
    except (ClipPublisherError, CompositionError, ValueError) as error:
        safe_print(f"post queue stopped: {error}")
        return 1
    for event in events:
        safe_print(f"{event.name}: {event.outcome} {event.detail}".rstrip())
    if not events:
        safe_print("nothing to do")
    return 0


if __name__ == "__main__":
    load_project_env(__file__)
    raise SystemExit(main(sys.argv[1:]))
