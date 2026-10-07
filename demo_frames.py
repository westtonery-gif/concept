#!/usr/bin/env python3
"""Draw the pictures of a story with Seedream (ADR-0088): the characters, then the shots.

    set -a && source .env && set +a
    python demo_frames.py sheets generation-tests/stories/<story>.txt
    python demo_frames.py shots  generation-tests/stories/<story>.txt [1,6,13]

`sheets` makes one full-body picture per character from the `look` written in the script's header
(`# key: look (voice)`), 9:16, one call each (about $0.03). Pictures already on disk are not paid
for again. Later shots take these sheets as references so a character stays the same character.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from demo import safe_print

from omemo_content_factory.adapters.frame_generator import FrameRequest
from omemo_content_factory.composition import build_frame_generator

WIDTH, HEIGHT = 1080, 1920
STYLE = (
    "3D animated feature-film style, Pixar-like, expressive stylised proportions, rich soft "
    "cinematic lighting, highly detailed textures, vertical 9:16 composition. "
    "No text, no captions, no logos, no watermark."
)
_HEADER = re.compile(r"^# ([a-z][a-z0-9_]*): (.+?)\s+\(([^()]+)\)\s*$")


def characters(script: Path) -> dict[str, str]:
    """The `look` of every character in the script's header."""
    found: dict[str, str] = {}
    for line in script.read_text().splitlines():
        match = _HEADER.match(line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def shots(script: Path, only: str) -> int:
    """Draw the shots of the saved storyboard, each with the sheets of whoever is in it."""
    folder = Path("generation-tests/frames") / script.stem
    board = json.loads((folder / "storyboard.json").read_text())
    wanted = {int(n) for n in only.split(",") if n.strip()} if only else None
    generator = build_frame_generator(os.environ)
    spent = 0
    for shot in board["shots"]:
        if wanted is not None and shot["number"] not in wanted:
            continue
        destination = folder / f"shot-{shot['number']:02d}.jpeg"
        if destination.exists():
            safe_print(f"[on disk] {destination.name}")
            continue
        references = tuple(str(folder / f"sheet-{key}.jpeg") for key in shot["cast"])
        image = generator.generate(
            FrameRequest(
                prompt=shot["prompt"],
                destination=str(destination),
                width=WIDTH,
                height=HEIGHT,
                references=references,
            )
        )
        spent += 1
        refs = ", ".join(shot["cast"]) or "-"
        safe_print(f"{destination.name}  {image.width}x{image.height}  refs: {refs}")
    safe_print(f"\n{spent} new picture(s), about ${spent * 0.03:.2f}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("sheets", "shots"):
        safe_print(
            "usage: python demo_frames.py sheets|shots <story.txt> [shot numbers, e.g. 1,6,13]"
        )
        return 2
    if argv[1] == "shots":
        return shots(Path(argv[2]), argv[3] if len(argv) > 3 else "")
    script = Path(argv[2])
    cast = characters(script)
    if not cast:
        safe_print("no `# key: look (voice)` header lines found in the script")
        return 1
    folder = Path("generation-tests/frames") / script.stem
    generator = build_frame_generator(os.environ)
    spent = 0
    for key, look in cast.items():
        destination = folder / f"sheet-{key}.jpeg"
        if destination.exists():
            safe_print(f"[on disk] {destination.name}")
            continue
        prompt = (
            "Character design, full body, a single character standing in a relaxed neutral pose "
            f"facing the camera on a plain soft grey studio backdrop. {look}. {STYLE}"
        )
        image = generator.generate(
            FrameRequest(prompt=prompt, destination=str(destination), width=WIDTH, height=HEIGHT)
        )
        spent += 1
        safe_print(f"{destination.name}  {image.width}x{image.height}")
    safe_print(f"\n{spent} new picture(s), about ${spent * 0.03:.2f}; folder: {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
