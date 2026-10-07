"""Turning a story script into shots, and reading what the storyboard writer answers (ADR-0089).

``storyboard_writer@v1`` answers in two flat string fields (ADR-0014): ``world`` — the place and
light every picture shares — and ``shots``, one shot per line, parts separated by ``|``::

    lines | cast | picture | motion        e.g.   3-4 | gramps_hal, nephew_dale | Medium shot, … | …

This module is the only judge of that grammar and of the limits that are arithmetic: the shots must
cover every line of dialogue once and in order, the cast must be characters of the story, a speaker
must be in the picture of the line they speak (unless it is an insert shot, ``-``), and the
sizes must be sane. Whether the pictures will be *good* is for the eye. It also builds the one
prompt a picture is drawn from — deterministically, so that the style and the setting can never
drift from shot to shot. Pure: standard library only, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.story_script import StoryScript
from omemo_content_factory.application.task_execution import TaskExecutor

__all__ = [
    "STORYBOARD_FIELDS",
    "STYLE",
    "Shot",
    "Storyboard",
    "StoryboardError",
    "StoryboardWriting",
    "decode_storyboard",
    "shot_prompt",
    "storyboard_input",
]

STORYBOARD_FIELDS = ("world", "shots")

STYLE = (
    "3D animated feature-film style, Pixar-like, expressive stylised proportions, rich soft "
    "cinematic lighting, highly detailed textures, vertical 9:16 composition, the lower fifth of "
    "the frame left uncluttered. No text, no captions, no logos, no watermark."
)
"""The look every picture shares; it lives here, in code, so it cannot drift between shots."""

_FRAMING = "The framing is exactly as described, and the characters are large in the frame."

MIN_SHOTS = 10
"""Fewer shots than this is a slideshow. There is no upper bound but the lines: a shot covers at
least one line, and the reference stories cut every few seconds (13–41 cuts in 66–108 s)."""
MAX_LINES_PER_SHOT = 3
MAX_CAST = 3
_MAX_FAULTS = 10
WORLD_WORDS = (30, 130)
PICTURE_WORDS = (20, 90)
"""The Prompt asks for 35–80 words; the decoder accepts from 20, so a writer who comes a little
short is not sent round again over one word."""
MOTION_WORDS = (4, 40)

_LINES = re.compile(r"(\d+)(?:\s*-\s*(\d+))?")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,23}")


class StoryboardError(Exception):
    """The storyboard breaks the grammar or a limit. Never guessed into something usable."""


@dataclass(frozen=True, slots=True)
class Shot:
    """One picture: the dialogue lines it covers (1-based, inclusive), its cast, what it shows."""

    first_line: int
    last_line: int
    cast: tuple[str, ...]
    picture: str
    motion: str


@dataclass(frozen=True, slots=True)
class Storyboard:
    """A decoded, checked storyboard."""

    world: str
    shots: tuple[Shot, ...]


@dataclass(frozen=True, slots=True)
class StoryboardWriting:
    """The storyboard role, compiled: its executor and the Schema its Output is checked against."""

    executor: TaskExecutor
    schema_binding: SchemaBinding


def storyboard_input(script: StoryScript) -> str:
    """What the storyboard writer is told: the cast, and the dialogue with its line numbers."""
    cast = "\n".join(f"{c.key} | {c.look}" for c in script.characters)
    lines = "\n".join(
        f"{number} | {line.speaker} | {line.spoken}" for number, line in enumerate(script.lines, 1)
    )
    return (
        f"Title: {script.title}\nPremise: {script.premise}\n\n"
        f"Characters (key | look):\n{cast}\n\nDialogue (line number | speaker | words):\n{lines}"
    )


def shot_prompt(storyboard: Storyboard, shot: Shot, script: StoryScript) -> str:
    """The single prompt a shot's picture is drawn from: the shot, the cast's looks, setting, style.

    The shot comes **first** and the setting after it: a long description of the place put in front
    pulled every picture into a wide view with tiny characters (measured on the first story). The
    character sheets travel as reference pictures; their looks are repeated in words as well,
    because a reference is a hint to the model and a sentence is an instruction.
    """
    looks = {c.key: c.look for c in script.characters}
    parts = [shot.picture, _FRAMING]
    if shot.cast:
        who = " ".join(f"{key} — {looks[key]}." for key in shot.cast)
        parts.append(f"The characters look exactly as in the reference pictures. {who}")
    parts += [f"Setting: {storyboard.world}", STYLE]
    return " ".join(parts)


def decode_storyboard(fields: Mapping[str, str], script: StoryScript) -> Storyboard:
    """Turn the writer's fields into a checked storyboard for ``script``, or raise."""
    missing = [name for name in STORYBOARD_FIELDS if not str(fields.get(name, "")).strip()]
    if missing:
        raise StoryboardError("the storyboard is missing: " + ", ".join(missing))
    world = fields["world"].strip()
    _words(world, WORLD_WORDS, "the world")
    shots = _shots(fields["shots"], script)
    if len(shots) < MIN_SHOTS:
        raise StoryboardError(f"a storyboard needs at least {MIN_SHOTS} shots, not {len(shots)}")
    expected = 1
    for number, shot in enumerate(shots, start=1):
        if shot.first_line != expected:
            raise StoryboardError(
                f"shot {number} starts at line {shot.first_line}; the next uncovered line is "
                f"{expected} — every line is covered once, in order"
            )
        expected = shot.last_line + 1
    if expected != len(script.lines) + 1:
        raise StoryboardError(
            f"the shots stop at line {expected - 1} of {len(script.lines)}; every line needs a shot"
        )
    return Storyboard(world=world, shots=tuple(shots))


def _shots(block: str, script: StoryScript) -> list[Shot]:
    """Every shot line is checked and **all** the faults are reported together: a writer sent back
    with one error at a time fixes one and trips on the next, and the repair round is paid for."""
    known = {c.key for c in script.characters}
    shots: list[Shot] = []
    faults: list[str] = []
    for number, raw in enumerate((r.strip() for r in block.splitlines()), start=1):
        if not raw:
            continue
        try:
            shots.append(_shot(raw, number, known, script))
        except StoryboardError as error:
            faults.append(str(error))
    if faults:
        shown = faults[:_MAX_FAULTS]
        more = f" (and {len(faults) - len(shown)} more)" if len(faults) > len(shown) else ""
        raise StoryboardError("; ".join(shown) + more)
    return shots


def _shot(raw: str, number: int, known: set[str], script: StoryScript) -> Shot:
    cells = [cell.strip() for cell in raw.split("|", 3)]
    if len(cells) != 4 or not all(cells):
        raise StoryboardError(f"shot line {number} is not lines | cast | picture | motion")
    first, last = _span(cells[0], number, len(script.lines))
    cast = _cast(cells[1], known, number)
    problems: list[str] = []
    for text, limits, what in (
        (cells[2], PICTURE_WORDS, "picture"),
        (cells[3], MOTION_WORDS, "motion"),
    ):
        try:
            _words(text, limits, f"the {what} of shot line {number}")
        except StoryboardError as error:
            problems.append(str(error))
    # A character the picture names, or who speaks in a shot that has a cast, is drawn whether or
    # not the writer listed them — so they are listed here, and their sheet becomes a reference.
    picture = cells[2].lower()
    mentioned = sorted((key for key in known if key in picture), key=picture.index)
    speakers = [script.lines[i - 1].speaker for i in range(first, last + 1)] if cast else []
    cast = (*cast, *dict.fromkeys(key for key in (*speakers, *mentioned) if key not in cast))
    if len(cast) > MAX_CAST:
        problems.append(
            f"shot line {number} has {len(cast)} characters once those named in its picture and "
            f"those who speak are counted; the limit is {MAX_CAST}"
        )
    if problems:
        raise StoryboardError("; ".join(problems))
    return Shot(first, last, cast, cells[2], cells[3])


def _span(text: str, number: int, total: int) -> tuple[int, int]:
    match = _LINES.fullmatch(text)
    if match is None:
        raise StoryboardError(f"shot line {number}: lines must look like 4 or 4-5")
    first = int(match.group(1))
    last = int(match.group(2) or match.group(1))
    if not 1 <= first <= last <= total:
        raise StoryboardError(f"shot line {number}: lines {text} are outside 1–{total}")
    if last - first + 1 > MAX_LINES_PER_SHOT:
        raise StoryboardError(f"shot line {number} covers more than {MAX_LINES_PER_SHOT} lines")
    return first, last


def _cast(text: str, known: set[str], number: int) -> tuple[str, ...]:
    if text == "-":
        return ()
    keys = tuple(part.strip() for part in text.split(","))
    for key in keys:
        if not _KEY.fullmatch(key) or key not in known:
            raise StoryboardError(f"shot line {number}: {key!r} is not a character of the story")
    if len(set(keys)) != len(keys):
        raise StoryboardError(f"shot line {number} names a character twice")
    if len(keys) > MAX_CAST:
        raise StoryboardError(f"shot line {number} has more than {MAX_CAST} characters")
    return keys


def _words(text: str, limits: tuple[int, int], what: str) -> None:
    count = len(text.split())
    if not limits[0] <= count <= limits[1]:
        raise StoryboardError(f"{what} has {count} words; it needs {limits[0]}–{limits[1]}")
