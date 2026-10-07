"""Tests for the story script decoder (GENERATION_ACCEPTANCE §9, SCR; ADR-0087).

The decoder judges the line grammar and the limits that are arithmetic; whether a story is good is
not its question.
"""

from __future__ import annotations

import pytest

from omemo_content_factory.application.story_script import (
    MAX_LINE_WORDS,
    StoryScriptError,
    decode_story_script,
    strip_audio_tags,
)

CHARACTERS = (
    "ray | orange fruit-headed boss in a red suit, a glowing '215 YEARS' band on his forehead | "
    "male, fifties, booming\n"
    "mimi | small round peach girl with a blue bow and a '4 months' band | female, young, bright"
)


def _dialogue(lines: int = 16, words: int = 9) -> str:
    rows = []
    for index in range(lines):
        speaker = "ray" if index % 2 == 0 else "mimi"
        sentence = " ".join(f"word{index}x{n}" for n in range(words))
        rows.append(f"{speaker} | {sentence}")
    return "\n".join(rows)


def _fields(**overrides: str) -> dict[str, str]:
    fields = {
        "title": "The Boss Who Counted Her Days",
        "premise": "A little peach carries a timer of years, and her boss spends them.",
        "characters": CHARACTERS,
        "dialogue": _dialogue(),
        "next_part": "Mimi finds out what the timer really measures.",
    }
    fields.update(overrides)
    return fields


def test_scr_01_a_good_script_decodes_with_its_measures() -> None:
    script = decode_story_script(_fields())
    assert [c.key for c in script.characters] == ["ray", "mimi"]
    assert script.characters[0].voice == "male, fifties, booming"
    assert len(script.lines) == 16 and script.word_count == 16 * 9
    assert script.estimated_seconds == pytest.approx(16 * 9 / 2.1)


def test_scr_02_audio_tags_stay_in_text_and_leave_spoken() -> None:
    dialogue = "ray | [laughs] Ha, no way, you wish.\n" + _dialogue(15)
    script = decode_story_script(_fields(dialogue=dialogue))
    assert script.lines[0].text.startswith("[laughs]")
    assert script.lines[0].spoken == "Ha, no way, you wish."
    assert strip_audio_tags("[sighs]  Fine. [whispers softly] Fine.") == "Fine. Fine."


@pytest.mark.parametrize("name", ["title", "premise", "characters", "dialogue", "next_part"])
def test_scr_03_every_field_is_required(name: str) -> None:
    with pytest.raises(StoryScriptError, match=name):
        decode_story_script(_fields(**{name: "  "}))


def test_scr_03_a_long_title_is_refused() -> None:
    with pytest.raises(StoryScriptError, match="title"):
        decode_story_script(_fields(title="x" * 91))


@pytest.mark.parametrize(
    "characters",
    [
        "ray | look | voice",  # one character
        "\n".join(f"c{i} | look | voice" for i in range(7)),  # seven
        "ray | look | voice\nray | other | voice",  # twice
        "Ray | look | voice\nmimi | look | voice",  # key not lowercase
        "ray | look\nmimi | look | voice",  # two parts
        "ray | look | voice\nmimi | | voice",  # empty part
    ],
)
def test_scr_04_the_cast_must_follow_the_grammar(characters: str) -> None:
    with pytest.raises(StoryScriptError):
        decode_story_script(_fields(characters=characters, dialogue=_dialogue()))


def test_scr_05_dialogue_speakers_must_be_declared_and_all_must_speak() -> None:
    with pytest.raises(StoryScriptError, match="'yan', 'zed'") as raised:
        decode_story_script(
            _fields(dialogue="zed | hi there friend\nyan | and me too\n" + _dialogue(14))
        )
    assert "lines 1, 2" in str(raised.value)
    only_ray = "\n".join(f"ray | {' '.join(['word'] * 9)}" for _ in range(16))
    with pytest.raises(StoryScriptError, match="never speak: mimi"):
        decode_story_script(_fields(dialogue=only_ray))


def test_scr_06_a_dialogue_line_is_not_key_pipe_text() -> None:
    with pytest.raises(StoryScriptError, match="line 1"):
        decode_story_script(_fields(dialogue="just words with no speaker\n" + _dialogue(15)))
    with pytest.raises(StoryScriptError, match="line 2"):
        decode_story_script(_fields(dialogue="ray | ok then\nmimi |\n" + _dialogue(14)))


def test_scr_07_line_counts_word_counts_and_the_opening_are_limited() -> None:
    with pytest.raises(StoryScriptError, match="lines of dialogue"):
        decode_story_script(_fields(dialogue=_dialogue(lines=13, words=12)))
    with pytest.raises(StoryScriptError, match="lines of dialogue"):
        decode_story_script(_fields(dialogue=_dialogue(lines=41, words=5)))
    with pytest.raises(StoryScriptError, match="spoken words"):
        decode_story_script(_fields(dialogue=_dialogue(lines=16, words=3)))
    with pytest.raises(StoryScriptError, match="spoken words"):
        decode_story_script(_fields(dialogue=_dialogue(lines=40, words=7)))
    with pytest.raises(StoryScriptError, match="limit is"):
        decode_story_script(
            _fields(dialogue=f"ray | {' '.join(['w'] * (MAX_LINE_WORDS + 1))}\n" + _dialogue(15))
        )
    with pytest.raises(StoryScriptError, match="first line"):
        decode_story_script(_fields(dialogue=f"ray | {' '.join(['w'] * 15)}\n" + _dialogue(15)))


@pytest.mark.parametrize(
    "line",
    ["ray | [laughs", "ray | laughs] now", "ray | [one two three four] no", "ray | [laughs]"],
)
def test_scr_08_tags_must_be_balanced_short_and_not_the_whole_line(line: str) -> None:
    with pytest.raises(StoryScriptError):
        decode_story_script(_fields(dialogue=line + "\n" + _dialogue(15)))
