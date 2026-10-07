#!/usr/bin/env python3
"""Bring the pictures of a voiced story to life with Seedance (ADR-0090).

    set -a && source .env && set +a
    python demo_animate.py plan  <story.txt>            # windows, seconds, estimated cost: free
    python demo_animate.py run   <story.txt> [1,6,13]   # submit and collect clips: PAID
    python demo_animate.py join  <story.txt>            # trim every clip to its window, join them

A shot's window comes from the real spoken lines (the sidecars `demo_dialogue.py` keeps in
generation-tests/dialogue/lines/): it starts where its first line starts and ends where the next
shot's first line starts, so the clips tile the voice track. A clip is asked for the next whole
second (Seedance takes whole seconds, 2-12) and trimmed to its window afterwards.

Jobs are remembered in `clips.json` BEFORE they are waited for: a re-run never submits a shot twice.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from demo import safe_print

from omemo_content_factory.adapters.shot_animator import (
    AnimationRequest,
    ShotAnimator,
    VideoJob,
    VideoJobState,
)
from omemo_content_factory.adapters.speech_synthesizer import SpokenWord, SynthesizedSpeech
from omemo_content_factory.composition import build_shot_animator
from omemo_content_factory.infrastructure.dialogue_mixer import DialogueLine, lay_out

_PRICE_PER_SECOND = 0.054  # 720p, Seedance 1.0 pro: $0.0025 per 1K tokens, measured 2026-09-27


def windows(story: Path) -> list[tuple[int, int]]:
    """Each shot's (start_ms, end_ms) on the voice track, from the spoken lines' real lengths."""
    folder = story.parent.parent / "frames" / story.stem
    board = json.loads((folder / "storyboard.json").read_text())
    sidecars = sorted((Path("generation-tests/dialogue/lines")).glob("*.json"))
    lines = []
    for sidecar in sidecars:
        saved = json.loads(sidecar.read_text())
        words = tuple(SpokenWord(**w) for w in saved["words"])
        speech = SynthesizedSpeech("x.wav", saved["duration_ms"], saved["sample_rate"], words)
        lines.append(DialogueLine("x", speech))
    starts, total = lay_out(lines)
    bounds = []
    for shot in board["shots"]:
        bounds.append(0 if shot["lines"][0] == 1 else starts[shot["lines"][0] - 1])
    bounds.append(total)
    return [(bounds[i], bounds[i + 1]) for i in range(len(board["shots"]))]


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("plan", "run", "join"):
        safe_print("usage: python demo_animate.py plan|run|join <story.txt> [shots]")
        return 2
    story = Path(argv[2])
    folder = Path("generation-tests/frames") / story.stem
    board = json.loads((folder / "storyboard.json").read_text())
    spans = windows(story)
    seconds = [min(12, max(2, math.ceil((end - start) / 1000))) for start, end in spans]
    if argv[1] == "plan":
        for shot, (start, end), secs in zip(board["shots"], spans, seconds, strict=True):
            safe_print(
                f"shot {shot['number']:>2}  {start / 1000:6.1f}-{end / 1000:6.1f}s  "
                f"window {(end - start) / 1000:4.1f}s -> ask {secs}s"
            )
        safe_print(
            f"\n{len(seconds)} clips, {sum(seconds)} s asked for, "
            f"about ${sum(seconds) * _PRICE_PER_SECOND:.2f}; "
            f"voice track {spans[-1][1] / 1000:.1f} s"
        )
        return 0
    if argv[1] == "join":
        return join(folder, spans)
    wanted = {int(n) for n in argv[3].split(",") if n.strip()} if len(argv) > 3 else None
    return run(folder, board, seconds, wanted)


def run(folder: Path, board: dict, seconds: list[int], wanted: set[int] | None) -> int:
    animator = build_shot_animator(os.environ)
    state_path = folder / "clips.json"
    state: dict[str, str] = json.loads(state_path.read_text()) if state_path.exists() else {}
    pending: dict[int, VideoJob] = {}
    for shot, secs in zip(board["shots"], seconds, strict=True):
        number = shot["number"]
        if wanted is not None and number not in wanted:
            continue
        if (folder / f"clip-{number:02d}.mp4").exists():
            safe_print(f"[on disk] clip-{number:02d}.mp4")
            continue
        if str(number) in state:
            pending[number] = VideoJob(state[str(number)])
            safe_print(f"[already submitted] shot {number}: {state[str(number)]}")
            continue
        job = animator.submit(
            AnimationRequest(
                frame=str(folder / f"shot-{number:02d}.jpeg"),
                prompt=shot["motion"] + " Smooth, natural cartoon-film motion, no cuts.",
                duration_s=secs,
            )
        )
        state[str(number)] = job.job_id  # remembered before anything is waited for
        state_path.write_text(json.dumps(state, indent=1))
        pending[number] = job
        safe_print(f"[submitted] shot {number} ({secs}s) -> {job.job_id}")
    return _wait(animator, folder, pending)


def _wait(animator: ShotAnimator, folder: Path, pending: dict[int, VideoJob]) -> int:
    deadline = time.monotonic() + 40 * 60
    while pending and time.monotonic() < deadline:
        for number, job in list(pending.items()):
            destination = folder / f"clip-{number:02d}.mp4"
            result = animator.collect(job, str(destination))
            if result.state is VideoJobState.PENDING:
                continue
            del pending[number]
            if result.state is VideoJobState.COMPLETED and result.video:
                safe_print(
                    f"[done] clip-{number:02d}.mp4  {result.video.duration_ms / 1000:.1f}s "
                    f"{result.video.width}x{result.video.height}"
                )
            else:
                safe_print(f"[{result.state.value.upper()}] shot {number}: {job.job_id}")
        if pending:
            time.sleep(10)
    if pending:
        safe_print(f"still running: {sorted(pending)} — run again to collect them")
    return 0


def join(folder: Path, spans: list[tuple[int, int]]) -> int:
    parts = []
    for index, (start, end) in enumerate(spans, start=1):
        clip = folder / f"clip-{index:02d}.mp4"
        if not clip.exists():
            safe_print(f"missing {clip.name}; join needs every clip")
            return 1
        trimmed = folder / f"trim-{index:02d}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(clip),
                "-t",
                f"{(end - start) / 1000:.3f}",
                "-vf",
                "scale=720:1280:flags=lanczos,fps=24,format=yuv420p",
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                str(trimmed),
            ],
            check=True,
        )
        parts.append(trimmed)
    listing = folder / "clips.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = folder / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(out),
        ],
        check=True,
    )
    safe_print(f"joined: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
