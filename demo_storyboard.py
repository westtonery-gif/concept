#!/usr/bin/env python3
"""Cut a saved story into shots with picture prompts (storyboard_writer@v1, ADR-0089).

    set -a && source .env && set +a
    python demo_storyboard.py generation-tests/stories/<story>.txt

Reads the script that `demo_story.py` saved, asks the writer for a storyboard, checks it with the
same decoder the department uses (one repair round if it is refused), prints the shots and writes
generation-tests/frames/<story>/storyboard.json with the final prompt of every shot.
About $0.03 per call.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from demo import safe_print

from omemo_content_factory.agents import storyboard_writer
from omemo_content_factory.application.story_script import StoryScript, decode_story_script
from omemo_content_factory.application.storyboard import (
    Storyboard,
    StoryboardError,
    decode_storyboard,
    min_shots_for,
    shot_prompt,
    storyboard_input,
)
from omemo_content_factory.composition import build_storyboard_writing
from omemo_content_factory.infrastructure.provider_model import client_for_role

_SHOTS_PER_LINE = 1.5
"""The references cut every one to two seconds: about one and a half shots per spoken line."""
_CAST = re.compile(r"^# ([a-z][a-z0-9_]*): (.+?)\s+\(([^()]+)\)\s*$")


def load_story(path: Path) -> StoryScript:
    """Rebuild the script `demo_story.py` saved (header comments + `key | voice | text` lines)."""
    title = premise = next_part = ""
    characters: list[str] = []
    dialogue: list[str] = []
    for raw in path.read_text().splitlines():
        if raw.startswith("# next part:"):
            next_part = raw.removeprefix("# next part:").strip()
        elif match := _CAST.match(raw):
            characters.append(" | ".join(match.groups()))
        elif raw.startswith("# ") and not title:
            title = raw[2:].strip()
        elif raw.startswith("# ") and not premise:
            premise = raw[2:].strip()
        elif raw.strip() and not raw.startswith("#"):
            speaker, _voice, text = (part.strip() for part in raw.split("|", 2))
            dialogue.append(f"{speaker} | {text}")
    return decode_story_script(
        {
            "title": title,
            "premise": premise,
            "characters": "\n".join(characters),
            "dialogue": "\n".join(dialogue),
            "next_part": next_part,
        }
    )


def _show(board: Storyboard, script: StoryScript, folder: Path, cost: float) -> int:
    safe_print(f"World: {board.world}\n")
    for number, shot in enumerate(board.shots, start=1):
        span = (
            f"{shot.first_line}-{shot.last_line}"
            if shot.last_line > shot.first_line
            else str(shot.first_line)
        )
        safe_print(f"{number:>2}. lines {span:<5} cast {', '.join(shot.cast) or '-'}")
        safe_print(f"    {shot.picture}\n    motion: {shot.motion}")
    safe_print(f"\n{len(board.shots)} shots; cost: ${cost:.4f}")
    out = folder / "storyboard.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "world": board.world,
                "shots": [
                    {
                        "number": number,
                        "lines": [shot.first_line, shot.last_line],
                        "cast": list(shot.cast),
                        "picture": shot.picture,
                        "motion": shot.motion,
                        "prompt": shot_prompt(board, shot, script),
                    }
                    for number, shot in enumerate(board.shots, start=1)
                ],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    safe_print(f"saved: {out}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        safe_print("usage: python demo_storyboard.py <story.txt>")
        return 2
    path = Path(argv[1])
    script = load_story(path)
    folder = Path("generation-tests/frames") / path.stem
    if "--reuse" in argv:  # decode the writer's last saved answer again; no model call, no cost
        saved = sorted(folder.glob("storyboard.raw-*.json"))[-1]
        board = decode_storyboard(
            json.loads(saved.read_text()), script, shots_per_line=_SHOTS_PER_LINE
        )
        return _show(board, script, folder, cost=0.0)
    writing = build_storyboard_writing(client_for_role(storyboard_writer.AGENT_REF, os.environ))
    need = min_shots_for(script, _SHOTS_PER_LINE)
    base = storyboard_input(script, min_shots=need)
    task_input, cost = base, 0.0
    for attempt in (1, 2):
        result = writing.executor.execute(task_input)
        cost += sum(float(m.cost.amount) for m in result.analytics)
        if not result.succeeded or not result.payload_fields:
            safe_print(f"The writer failed: {result.failure_reason}")
            return 1
        raw = Path("generation-tests/frames") / path.stem / f"storyboard.raw-{attempt}.json"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(json.dumps(dict(result.payload_fields), ensure_ascii=False, indent=1))
        try:
            board = decode_storyboard(result.payload_fields, script, shots_per_line=_SHOTS_PER_LINE)
            break
        except StoryboardError as error:
            safe_print(f"Attempt {attempt}: the storyboard does not pass the decoder: {error}")
            if attempt == 2:
                safe_print("\n".join(f"[{k}]\n{v}\n" for k, v in result.payload_fields.items()))
                return 1
            task_input = (
                f"{base}\n\nТвоя предыдущая раскадровка отклонена программой: {error}. "
                "Исправь это и верни раскадровку заново."
            )
    return _show(board, script, folder, cost)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
