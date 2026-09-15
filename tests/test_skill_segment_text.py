"""Tests for ``segment_text@v1`` (ADR-0021 §4; SKILL_ACCEPTANCE.md §3, SEG)."""

from __future__ import annotations

from typing import Any

import pytest

from omemo_content_factory.domain.skill import InvalidSkillInputError
from omemo_content_factory.skills.segment_text import SegmentText, SegmentTextInput

_SKILL = SegmentText()

_SAMPLE = (
    "Витамин D помогает усваивать кальций. Его дефицит встречается часто…\n\n"
    "Сдайте анализ   25(OH)D перед приёмом добавок! Дозу подбирает врач.\n"
    "\n  \n"
    "Суперкалифрагилистикэкспиалидоциус — очень длинное слово."
)


def _segments(text: str, max_chars: int) -> tuple[str, ...]:
    return _SKILL.apply(SegmentTextInput(text=text, max_chars=max_chars)).segments


def test_seg_01_short_text_is_one_segment() -> None:
    assert _segments("Коротко и ясно.", 100) == ("Коротко и ясно.",)


def test_seg_02_whole_sentences_are_packed_greedily() -> None:
    assert _segments("Один. Два два. Три три три.", 14) == ("Один. Два два.", "Три три три.")


def test_seg_03_paragraphs_are_never_merged() -> None:
    assert _segments("А.\n\nБ.\n  \nВ.", 100) == ("А.", "Б.", "В.")


def test_seg_04_over_long_sentence_is_packed_word_by_word() -> None:
    assert _segments("alpha beta gamma delta", 11) == ("alpha beta", "gamma delta")


def test_seg_05_over_long_word_is_cut_at_the_limit() -> None:
    assert _segments("abcdefgh", 3) == ("abc", "def", "gh")


@pytest.mark.parametrize("max_chars", range(1, 60))
def test_seg_06_limit_holds_and_no_character_is_lost(max_chars: int) -> None:
    segments = _segments(_SAMPLE, max_chars)
    for segment in segments:
        assert 1 <= len(segment) <= max_chars
        assert segment == segment.strip()
        assert "  " not in segment
    assert "".join("".join(s.split()) for s in segments) == "".join(_SAMPLE.split())


@pytest.mark.parametrize("text", ["", "   ", "\n\n\t\n"])
def test_seg_07_blank_text_has_no_segments(text: str) -> None:
    assert _segments(text, 10) == ()


def test_seg_08_whitespace_is_normalised_to_single_spaces() -> None:
    assert _segments("a  \n b\tc.", 100) == ("a b c.",)


@pytest.mark.parametrize(
    ("text", "max_chars"),
    [("x", 0), ("x", -5), ("x", True), ("x", 1.5), (None, 10)],
)
def test_seg_09_invalid_input_is_refused(text: Any, max_chars: Any) -> None:
    with pytest.raises(InvalidSkillInputError):
        SegmentTextInput(text=text, max_chars=max_chars)
