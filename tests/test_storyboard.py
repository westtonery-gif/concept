"""Tests for the storyboard decoder and prompt builder (GENERATION_ACCEPTANCE §11, SBD).

ADR-0089.
"""

from __future__ import annotations

import itertools

import pytest

from omemo_content_factory.application.story_script import StoryScript, decode_story_script
from omemo_content_factory.application.storyboard import (
    STYLE,
    StoryboardError,
    decode_storyboard,
    shot_prompt,
    shot_windows,
    storyboard_input,
)

WORLD = " ".join(["A weathered stone lighthouse at dusk, warm lamplight and cold blue sea air"] * 3)
PICTURE = " ".join(["Medium shot from slightly below, the speaker leans forward with a grin"] * 3)
MOTION = "The camera pushes in slowly while the speaker gestures."


def _script() -> StoryScript:
    rows = []
    for index in range(16):
        speaker = "ray" if index % 2 == 0 else "mimi"
        rows.append(f"{speaker} | " + " ".join(f"w{index}x{n}" for n in range(9)))
    return decode_story_script(
        {
            "title": "T",
            "premise": "P",
            "characters": (
                "ray | orange boss with a glowing hat | male\nmimi | small peach girl | female"
            ),
            "dialogue": "\n".join(rows),
            "next_part": "N",
        }
    )


def _shots(spans: list[tuple[str, str]] | None = None) -> str:
    """One shot per line for 16 lines unless spans are given as (lines, cast)."""
    spans = spans or [(str(i), "ray" if i % 2 == 1 else "mimi") for i in range(1, 17)]
    return "\n".join(f"{lines} | {cast} | {PICTURE} | {MOTION}" for lines, cast in spans)


def _fields(**overrides: str) -> dict[str, str]:
    fields = {"world": WORLD, "shots": _shots()}
    fields.update(overrides)
    return fields


def test_sbd_01_a_good_storyboard_decodes() -> None:
    board = decode_storyboard(_fields(), _script())
    assert len(board.shots) == 16
    assert (board.shots[0].first_line, board.shots[0].last_line, board.shots[0].cast) == (
        1,
        1,
        ("ray",),
    )
    assert board.world == WORLD.strip()


def test_sbd_02_shots_may_cover_several_lines_and_insert_shots_have_no_cast() -> None:
    spans = [("1-2", "ray, mimi"), ("3", "-")] + [
        (str(i), "ray" if i % 2 else "mimi") for i in range(4, 17)
    ]
    board = decode_storyboard(_fields(shots=_shots(spans)), _script())
    assert board.shots[0].cast == ("ray", "mimi") and board.shots[1].cast == ()


@pytest.mark.parametrize("name", ["world", "shots"])
def test_sbd_03_both_fields_are_required(name: str) -> None:
    with pytest.raises(StoryboardError, match=name):
        decode_storyboard(_fields(**{name: " "}), _script())


def test_sbd_04_every_line_is_covered_and_in_order() -> None:
    gap = [(str(i), "ray" if i % 2 else "mimi") for i in range(1, 17) if i != 5]
    with pytest.raises(StoryboardError, match="must start at line 5"):
        decode_storyboard(_fields(shots=_shots(gap)), _script())
    backwards = [("1-2", "ray, mimi"), ("1", "ray")] + [
        (str(i), "ray" if i % 2 else "mimi") for i in range(3, 17)
    ]
    with pytest.raises(StoryboardError, match="must start at line 3"):
        decode_storyboard(_fields(shots=_shots(backwards)), _script())
    short = [(str(i), "ray" if i % 2 else "mimi") for i in range(1, 16)]
    with pytest.raises(StoryboardError, match="every line needs a shot"):
        decode_storyboard(_fields(shots=_shots(short)), _script())


def test_sbd_04_shots_may_share_a_line_up_to_three_times() -> None:
    def spans(extra: int) -> list[tuple[str, str]]:
        shared = [("1", "ray")] * extra  # `extra` shots on line 1
        return shared + [(str(i), "ray" if i % 2 else "mimi") for i in range(2, 17)]

    board = decode_storyboard(_fields(shots=_shots(spans(3))), _script())
    assert [s.first_line for s in board.shots[:4]] == [1, 1, 1, 2]
    with pytest.raises(StoryboardError, match="already has 3 shots"):
        decode_storyboard(_fields(shots=_shots(spans(4))), _script())


def test_sbd_11_shared_lines_split_their_time_and_the_windows_tile_the_track() -> None:
    script = _script()
    spans = [("1", "ray"), ("1", "ray"), ("2-3", "mimi, ray"), ("3", "ray"), ("4", "mimi")] + [
        (str(i), "ray" if i % 2 else "mimi") for i in range(5, 17)
    ]
    board = decode_storyboard(_fields(shots=_shots(spans)), script)
    starts = [250 + 1000 * i for i in range(16)]  # sixteen lines, 1000 ms each
    windows = shot_windows(board, starts, [1000] * 16, 16_600)
    assert windows[0] == (0, 750)  # first half of line 1, from the very start
    assert windows[1] == (750, 1250)  # second half of line 1
    assert windows[2] == (1250, 2750)  # line 2 whole, then half of line 3 (it is shared)
    assert windows[3] == (2750, 3250)
    assert windows[-1][1] == 16_600
    assert all(a[1] == b[0] for a, b in itertools.pairwise(windows))


@pytest.mark.parametrize("lines", ["x", "0", "3-2", "17", "1-4", "1 - 2 - 3"])
def test_sbd_05_the_lines_field_must_be_a_sane_span(lines: str) -> None:
    spans = [(lines, "ray")] + [(str(i), "ray" if i % 2 else "mimi") for i in range(2, 17)]
    with pytest.raises(StoryboardError):
        decode_storyboard(_fields(shots=_shots(spans)), _script())


def test_sbd_06_the_cast_must_be_characters() -> None:
    for cast in ("zed", "ray, ray", "ray, mimi, ray, mimi"):
        spans = [("1", cast)] + [(str(i), "ray" if i % 2 else "mimi") for i in range(2, 17)]
        with pytest.raises(StoryboardError):
            decode_storyboard(_fields(shots=_shots(spans)), _script())


def test_sbd_06_a_speaker_the_writer_left_out_of_the_cast_is_added() -> None:
    wrong = [("1", "mimi")] + [(str(i), "ray" if i % 2 else "mimi") for i in range(2, 17)]
    board = decode_storyboard(_fields(shots=_shots(wrong)), _script())
    assert board.shots[0].cast == ("mimi", "ray")  # line 1 is spoken by ray


def test_sbd_06_a_character_named_in_the_picture_is_added_to_the_cast() -> None:
    drawn = _shots().replace(PICTURE, PICTURE + " with mimi hiding behind", 1)
    board = decode_storyboard(_fields(shots=drawn), _script())
    assert board.shots[0].cast == ("ray", "mimi")
    insert = _shots([("1", "-")] + [(str(i), "ray" if i % 2 else "mimi") for i in range(2, 17)])
    assert decode_storyboard(_fields(shots=insert), _script()).shots[0].cast == ()


def test_sbd_07_sizes_and_grammar_are_enforced() -> None:
    with pytest.raises(StoryboardError, match="the world has"):
        decode_storyboard(_fields(world="too short"), _script())
    bad_picture = _shots().replace(PICTURE, "too short", 1)
    with pytest.raises(StoryboardError, match="picture"):
        decode_storyboard(_fields(shots=bad_picture), _script())
    bad_motion = _shots().replace(MOTION, "moves", 1)
    with pytest.raises(StoryboardError, match="motion"):
        decode_storyboard(_fields(shots=bad_motion), _script())
    with pytest.raises(StoryboardError, match=r"lines \| cast"):
        decode_storyboard(_fields(shots="1 | ray | only three"), _script())
    few = _shots([("1-3", "ray, mimi"), ("4-6", "ray, mimi"), ("7-9", "ray, mimi")])
    with pytest.raises(StoryboardError, match="at least 10 shots"):
        decode_storyboard(_fields(shots=few), _script())


def test_sbd_08_the_input_numbers_the_dialogue_and_lists_the_cast() -> None:
    text = storyboard_input(_script())
    assert "ray | orange boss with a glowing hat" in text
    assert "1 | ray | w0x0" in text and "16 | mimi |" in text


def test_sbd_09_a_shot_prompt_puts_the_shot_first_then_looks_setting_and_style() -> None:
    script = _script()
    board = decode_storyboard(_fields(), script)
    prompt = shot_prompt(board, board.shots[1], script)
    assert prompt.startswith(board.shots[1].picture) and prompt.endswith(STYLE)
    assert prompt.index(board.world) < prompt.index(STYLE)
    assert board.world in prompt and board.shots[1].picture in prompt
    assert "mimi — small peach girl" in prompt and "ray — orange boss" not in prompt
    insert = decode_storyboard(
        _fields(
            shots=_shots(
                [("1", "-"), *[(str(i), "ray" if i % 2 else "mimi") for i in range(2, 17)]]
            )
        ),
        script,
    )
    assert "reference" not in shot_prompt(insert, insert.shots[0], script)


def test_sbd_10_all_the_faults_are_reported_together() -> None:
    spans = [(str(i), "ray" if i % 2 else "mimi") for i in range(1, 17)]
    lines = _shots(spans).splitlines()
    lines[2] = lines[2].replace(PICTURE, PICTURE + " with mimi")  # mimi is simply added
    lines[5] = lines[5].replace(PICTURE, "too short")
    lines[7] = lines[7].replace("mimi |", "zed |", 1)
    with pytest.raises(StoryboardError) as raised:
        decode_storyboard(_fields(shots="\n".join(lines)), _script())
    message = str(raised.value)
    assert "shot line 6" in message and "picture" in message
    assert "shot line 8" in message and "'zed'" in message


def test_sbd_12_a_pacing_floor_asks_for_reaction_shots() -> None:
    script = _script()
    assert decode_storyboard(_fields(), script)  # one shot per line is fine without a floor
    with pytest.raises(StoryboardError, match="at least 24 shots, not 16"):
        decode_storyboard(_fields(), script, shots_per_line=1.5)
    assert "AT LEAST 24 shots" in storyboard_input(script, min_shots=24)
    assert "AT LEAST" not in storyboard_input(script)
