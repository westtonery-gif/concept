"""Cut one episode file into fixed-length clips with burnt-in captions (no QA, board or posting).

    python demo_cut.py <file name under OMEMO_EPISODE_ROOT> [output folder] [--minutes N]
                       [--skip START-END ...]

``--skip`` (seconds; END may be ``end``) is the manual fallback when the titles cannot be found
against sibling episodes of the same series (ADR-0074): the stretch is left out of every clip, and
repeating the option adds more.

The same pieces the clipping department uses (ADR-0059/0064/0074): whisper.cpp and ffmpeg index the
episode, the opening and closing titles are found against the sibling episodes in the same folder
and left out, ``CHUNK`` mode cuts pieces of the asked length at the nearest speech pause, and
``FfmpegClipRenderer`` draws the captions in ``OMEMO_CLIP_CAPTION_COLOR`` (default white) on the
configured canvas. Nothing is judged by a model and nothing is published; the clips are files.

Run after loading ``.env``: ``set -a && source .env && set +a`` (nothing loads it for you).
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

from demo import safe_print

from omemo_content_factory.adapters.clip_renderer import ClipRenderRequest
from omemo_content_factory.adapters.episode_board import ClipMode
from omemo_content_factory.adapters.footage_index import SkipZone
from omemo_content_factory.application.clip_plan import plan_clips
from omemo_content_factory.composition import (
    build_clip_renderer,
    build_clip_settings,
    build_episode_source,
    build_footage_index,
)


def main(argv: list[str]) -> int:
    minutes = 2
    args = list(argv)
    if "--minutes" in args:
        at = args.index("--minutes")
        minutes = int(args[at + 1])
        del args[at : at + 2]
    manual: list[tuple[str, str]] = []
    while "--skip" in args:
        at = args.index("--skip")
        start, _, end = args[at + 1].partition("-")
        manual.append((start, end))
        del args[at : at + 2]
    if not args:
        safe_print(__doc__ or "")
        return 2
    source_ref = args[0]
    out = Path(args[1] if len(args) > 1 else "clips/cut")
    environ = os.environ

    located = build_episode_source(environ).locate(source_ref)
    if located is None:
        safe_print(f"no such file under OMEMO_EPISODE_ROOT: {source_ref}")
        return 1

    safe_print("indexing (whisper + scene + title search) — several minutes ...")
    indexed = build_footage_index(environ).index(located)
    if manual:
        zones = tuple(
            SkipZone(
                start_ms=round(float(start) * 1000),
                end_ms=indexed.duration_ms if end == "end" else round(float(end) * 1000),
            )
            for start, end in manual
        )
        indexed = dataclasses.replace(indexed, skips=tuple(sorted(zones, key=lambda z: z.start_ms)))
    safe_print(f"duration {indexed.duration_ms / 1000:.1f}s, speech spans {len(indexed.speech)}")
    for zone in indexed.skips:
        safe_print(f"  skipped: {zone.start_ms / 1000:.1f}s - {zone.end_ms / 1000:.1f}s")

    settings = build_clip_settings(environ)
    length_ms = minutes * 60_000
    plan = plan_clips(
        indexed,
        episode_ref=Path(source_ref).stem,
        mode=ClipMode.CHUNK,
        chunk_ms=length_ms,
        max_ms=length_ms,
        pause_tolerance_ms=settings.pause_tolerance_ms,
    )
    safe_print(f"{len(plan.clips)} clips planned")

    renderer = build_clip_renderer(environ)
    out.mkdir(parents=True, exist_ok=True)
    for clip in plan.clips:
        destination = out / f"{plan.episode_ref}-{clip.index:02d}.mp4"
        rendered = renderer.render(
            ClipRenderRequest(
                located=located,
                start_ms=clip.start_ms,
                end_ms=clip.end_ms,
                captions=clip.captions,
                destination=str(destination),
            )
        )
        safe_print(
            f"clip {clip.index:02d}: {clip.start_ms / 1000:.1f}-{clip.end_ms / 1000:.1f}s "
            f"-> {rendered.path} ({rendered.duration_ms / 1000:.1f}s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
