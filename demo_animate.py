#!/usr/bin/env python3
"""Bring the pictures of a voiced story to life (ADR-0090, ADR-0091).

    set -a && source .env && set +a
    python demo_animate.py plan  <story.txt>            # windows, which shots move, the price: free
    python demo_animate.py run   <story.txt> [1,6,13]   # submit and collect Seedance clips: PAID
    python demo_animate.py join  <story.txt>            # trim, zoom and join everything

A shot's window comes from the real spoken lines (the sidecars `demo_dialogue.py` keeps beside its
lines, `generation-tests/dialogue/<story>/lines/`); shots that share a line split its time.
A window of 2 s or more is animated by Seedance (asked for the next whole second, trimmed after); a
shorter one — the quick reactions that make these stories cut every second — is its picture with a
fast punch-in made by ffmpeg, which costs nothing.

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
from omemo_content_factory.application.storyboard import Shot, Storyboard, shot_windows
from omemo_content_factory.composition import build_shot_animator
from omemo_content_factory.infrastructure.dialogue_mixer import DialogueLine, lay_out

_PRICE_PER_SECOND = 0.054  # 720p, Seedance 1.0 pro: $0.0025 per 1K tokens, measured 2026-09-27
_MIN_ANIMATED_MS = 2000
"""Seedance clips last 2 s at least, so a shorter window cannot be a clip: it is a punch-in."""


def plan(story: Path) -> tuple[Storyboard, list[tuple[int, int]]]:
    """The storyboard and every shot's (start_ms, end_ms) on the voice track."""
    folder = Path("generation-tests/frames") / story.stem
    saved = json.loads((folder / "storyboard.json").read_text())
    board = Storyboard(
        world=saved["world"],
        shots=tuple(
            Shot(s["lines"][0], s["lines"][1], tuple(s["cast"]), s["picture"], s["motion"])
            for s in saved["shots"]
        ),
    )
    sidecars = sorted((Path("generation-tests/dialogue") / story.stem / "lines").glob("*.json"))
    lines = []
    for sidecar in sidecars:
        info = json.loads(sidecar.read_text())
        words = tuple(SpokenWord(**w) for w in info["words"])
        speech = SynthesizedSpeech("x.wav", info["duration_ms"], info["sample_rate"], words)
        lines.append(DialogueLine("x", speech))
    starts, total = lay_out(lines)
    durations = [line.speech.duration_ms for line in lines]
    return board, shot_windows(board, starts, durations, total)


def seconds_for(window: tuple[int, int]) -> int | None:
    """Whole seconds to ask Seedance for, or ``None`` when the window is too short to animate."""
    length = window[1] - window[0]
    return None if length < _MIN_ANIMATED_MS else min(12, math.ceil(length / 1000))


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("plan", "run", "join"):
        safe_print("usage: python demo_animate.py plan|run|join <story.txt> [shots]")
        return 2
    story = Path(argv[2])
    folder = Path("generation-tests/frames") / story.stem
    board, windows = plan(story)
    asked = [seconds_for(w) for w in windows]
    if argv[1] == "plan":
        for number, (window, secs) in enumerate(zip(windows, asked, strict=True), start=1):
            kind = f"Seedance {secs}s" if secs else "punch-in"
            safe_print(
                f"shot {number:>2}  {window[0] / 1000:6.1f}-{window[1] / 1000:6.1f}s  "
                f"{(window[1] - window[0]) / 1000:4.1f}s  {kind}"
            )
        moving = [s for s in asked if s]
        safe_print(
            f"\n{len(windows)} shots: {len(moving)} animated ({sum(moving)} s asked, about "
            f"${sum(moving) * _PRICE_PER_SECOND:.2f}), {len(windows) - len(moving)} punch-ins; "
            f"voice track {windows[-1][1] / 1000:.1f} s"
        )
        return 0
    if argv[1] == "join":
        return join(folder, windows, asked)
    wanted = {int(n) for n in argv[3].split(",") if n.strip()} if len(argv) > 3 else None
    return run(folder, board, asked, wanted)


def run(folder: Path, board: Storyboard, asked: list[int | None], wanted: set[int] | None) -> int:
    animator = build_shot_animator(os.environ)
    state_path = folder / "clips.json"
    state: dict[str, str] = json.loads(state_path.read_text()) if state_path.exists() else {}
    pending: dict[int, VideoJob] = {}
    for number, (shot, secs) in enumerate(zip(board.shots, asked, strict=True), start=1):
        if secs is None or (wanted is not None and number not in wanted):
            continue
        if (folder / f"clip-{number:02d}.mp4").exists():
            safe_print(f"[on disk] clip-{number:02d}.mp4")
        elif str(number) in state:
            pending[number] = VideoJob(state[str(number)])
            safe_print(f"[already submitted] shot {number}: {state[str(number)]}")
        else:
            job = animator.submit(
                AnimationRequest(
                    frame=str(folder / f"shot-{number:02d}.jpeg"),
                    prompt=shot.motion + " Smooth, lively cartoon-film motion, no cuts.",
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
            result = animator.collect(job, str(folder / f"clip-{number:02d}.mp4"))
            if result.state is VideoJobState.PENDING:
                continue
            del pending[number]
            if result.state is VideoJobState.COMPLETED and result.video:
                safe_print(f"[done] clip-{number:02d}.mp4  {result.video.duration_ms / 1000:.1f}s")
            else:
                safe_print(f"[{result.state.value.upper()}] shot {number}: {job.job_id}")
        if pending:
            time.sleep(10)
    if pending:
        safe_print(f"still running: {sorted(pending)} — run again to collect them")
    return 0


def join(folder: Path, windows: list[tuple[int, int]], asked: list[int | None]) -> int:
    parts = []
    for index, ((start, end), secs) in enumerate(zip(windows, asked, strict=True), start=1):
        length = (end - start) / 1000
        out = folder / f"trim-{index:02d}.mp4"
        if secs is not None:
            clip = folder / f"clip-{index:02d}.mp4"
            if not clip.exists():
                safe_print(f"missing {clip.name}; join needs every animated clip")
                return 1
            command = ["-i", str(clip), "-t", f"{length:.3f}", "-vf", _SCALE]
        else:
            picture = folder / f"shot-{index:02d}.jpeg"
            frames = max(2, round(length * 24))
            # a fast punch-in; the direction alternates so a run of reactions is not uniform
            grow = "min(1+0.006*on,1.3)" if index % 2 else "max(1.3-0.006*on,1)"
            zoom = (
                f"zoompan=z='{grow}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}"
                ":s=720x1280:fps=24,format=yuv420p"
            )
            command = ["-i", str(picture), "-vf", zoom, "-frames:v", str(frames)]
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                *command,
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                str(out),
            ],
            check=True,
        )
        parts.append(out)
    listing = folder / "clips.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    joined = folder / "silent.mp4"
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
            str(joined),
        ],
        check=True,
    )
    safe_print(f"joined: {joined}")
    return 0


_SCALE = "scale=720:1280:flags=lanczos,fps=24,format=yuv420p"

if __name__ == "__main__":
    sys.exit(main(sys.argv))
