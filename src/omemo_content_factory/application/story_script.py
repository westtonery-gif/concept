"""Reading a story script the writer produced — deterministically (ADR-0087).

``story_writer@v1`` answers in flat string fields, because that is the shape a structured Output
has here (ADR-0014). Two of them carry lists in a plain line grammar, one item per line, parts
separated by ``|``::

    characters:   key | look | voice
    dialogue:     key | what they say, with optional [audio tags]

This module is the only judge of that grammar and of the limits that are arithmetic rather than
taste — how many lines, how long, who speaks. Whether the story is *good* is a different question
for a different reader; a script that fails here never reaches a voice, which is cheaper than
paying for a voice to find out. Pure: standard library only, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import TaskExecutor

__all__ = [
    "MAX_LINE_WORDS",
    "MAX_TITLE_CHARS",
    "StoryCharacter",
    "StoryLine",
    "StoryScript",
    "StoryScriptError",
    "StoryWriting",
    "decode_story_script",
    "strip_audio_tags",
]

STORY_FIELDS = ("title", "premise", "characters", "dialogue", "next_part")

MAX_TITLE_CHARS = 90
MIN_CHARACTERS, MAX_CHARACTERS = 2, 6
MIN_LINES, MAX_LINES = 14, 40
MAX_FIRST_LINE_WORDS = 14
"""The opening line is a conflict or a shock, so it is short (the reference stories all open so)."""
MAX_LINE_WORDS = 35
MIN_TOTAL_WORDS, MAX_TOTAL_WORDS = 110, 240
WORDS_PER_SECOND = 2.1
"""Spoken words per second of finished track, pauses between lines included.

Measured on the first voiced story (2026-10-07): 259 words came out as 125 seconds, so the pace of
the speech alone (about 2.6) is not the pace of the video.
"""

_KEY = re.compile(r"[a-z][a-z0-9_]{0,23}")
_TAG = re.compile(r"\[([^\[\]]{1,30})\]")
_MAX_TAG_WORDS = 3


class StoryScriptError(Exception):
    """The script breaks the grammar or a limit. Never guessed into something usable."""


@dataclass(frozen=True, slots=True)
class StoryCharacter:
    """One character: a key the dialogue uses, how to draw them, how they sound."""

    key: str
    look: str
    voice: str


@dataclass(frozen=True, slots=True)
class StoryLine:
    """One line of dialogue. ``text`` keeps the audio tags; ``spoken`` is what is actually said."""

    speaker: str
    text: str
    spoken: str


@dataclass(frozen=True, slots=True)
class StoryScript:
    """A decoded, checked script."""

    title: str
    premise: str
    characters: tuple[StoryCharacter, ...]
    lines: tuple[StoryLine, ...]
    next_part: str

    @property
    def word_count(self) -> int:
        """Spoken words over the whole script, audio tags not counted."""
        return sum(len(line.spoken.split()) for line in self.lines)

    @property
    def estimated_seconds(self) -> float:
        """How long the speech runs at a conversational pace, pauses not included."""
        return self.word_count / WORDS_PER_SECOND


@dataclass(frozen=True, slots=True)
class StoryWriting:
    """The story role, compiled: its executor and the Schema its Output is validated against."""

    executor: TaskExecutor
    schema_binding: SchemaBinding


def strip_audio_tags(text: str) -> str:
    """``[laughs] Ha, no.`` → ``Ha, no.`` — what a voice that cannot act on tags should read."""
    return " ".join(_TAG.sub(" ", text).split())


def decode_story_script(fields: Mapping[str, str]) -> StoryScript:
    """Turn the writer's fields into a checked script, or raise ``StoryScriptError``."""
    missing = [name for name in STORY_FIELDS if not str(fields.get(name, "")).strip()]
    if missing:
        raise StoryScriptError("the script is missing: " + ", ".join(missing))
    title = fields["title"].strip()
    if len(title) > MAX_TITLE_CHARS:
        raise StoryScriptError(f"the title is longer than {MAX_TITLE_CHARS} characters")
    characters = _characters(fields["characters"])
    lines = _lines(fields["dialogue"], {character.key for character in characters})
    script = StoryScript(
        title=title,
        premise=fields["premise"].strip(),
        characters=characters,
        lines=lines,
        next_part=fields["next_part"].strip(),
    )
    silent = {character.key for character in characters} - {line.speaker for line in lines}
    if silent:
        raise StoryScriptError("characters who never speak: " + ", ".join(sorted(silent)))
    if not MIN_TOTAL_WORDS <= script.word_count <= MAX_TOTAL_WORDS:
        raise StoryScriptError(
            f"the dialogue has {script.word_count} spoken words; "
            f"it needs {MIN_TOTAL_WORDS}–{MAX_TOTAL_WORDS}"
        )
    return script


def _rows(block: str, parts: int, what: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for number, raw in enumerate(block.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        cells = [cell.strip() for cell in line.split("|", parts - 1)]
        if len(cells) != parts or not all(cells):
            raise StoryScriptError(f"{what} line {number} is not {' | '.join(['…'] * parts)}")
        rows.append(cells)
    return rows


def _characters(block: str) -> tuple[StoryCharacter, ...]:
    characters = [StoryCharacter(*row) for row in _rows(block, 3, "a characters")]
    if not MIN_CHARACTERS <= len(characters) <= MAX_CHARACTERS:
        raise StoryScriptError(f"a story needs {MIN_CHARACTERS}–{MAX_CHARACTERS} characters")
    keys = [character.key for character in characters]
    for key in keys:
        if not _KEY.fullmatch(key):
            raise StoryScriptError(f"character key {key!r} must be lowercase letters, digits, _")
    if len(set(keys)) != len(keys):
        raise StoryScriptError("a character key is used twice")
    return tuple(characters)


def _lines(block: str, known: set[str]) -> tuple[StoryLine, ...]:
    rows = _rows(block, 2, "a dialogue")
    strangers = sorted({speaker for speaker, _ in rows if speaker not in known})
    if strangers:
        numbers = [str(n) for n, (speaker, _) in enumerate(rows, 1) if speaker in strangers]
        raise StoryScriptError(
            f"dialogue lines {', '.join(numbers)} are spoken by "
            + ", ".join(repr(name) for name in strangers)
            + ", who are not in the characters; add them to characters or give the lines to "
            "someone who is"
        )
    lines: list[StoryLine] = []
    for number, (speaker, text) in enumerate(rows, start=1):
        _check_tags(text, number)
        spoken = strip_audio_tags(text)
        words = len(spoken.split())
        if not words:
            raise StoryScriptError(f"dialogue line {number} has no spoken words")
        if words > MAX_LINE_WORDS:
            raise StoryScriptError(
                f"dialogue line {number} has {words} words; the limit is {MAX_LINE_WORDS}"
            )
        lines.append(StoryLine(speaker, text, spoken))
    if not MIN_LINES <= len(lines) <= MAX_LINES:
        raise StoryScriptError(f"a story needs {MIN_LINES}–{MAX_LINES} lines of dialogue")
    if len(lines[0].spoken.split()) > MAX_FIRST_LINE_WORDS:
        raise StoryScriptError(f"the first line is longer than {MAX_FIRST_LINE_WORDS} words")
    return tuple(lines)


def _check_tags(text: str, number: int) -> None:
    for tag in _TAG.findall(text):
        if len(tag.split()) > _MAX_TAG_WORDS:
            raise StoryScriptError(
                f"dialogue line {number} has an audio tag over {_MAX_TAG_WORDS} words"
            )
    if "[" in _TAG.sub("", text) or "]" in _TAG.sub("", text):
        raise StoryScriptError(f"dialogue line {number} has an unbalanced bracket")
