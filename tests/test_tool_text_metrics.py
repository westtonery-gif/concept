"""Tests for ``text_metrics@v1`` (ADR-0022 §5). Maps TOOL_ACCEPTANCE.md §5 (TXM)."""

from __future__ import annotations

import pytest

from omemo_content_factory.tools.contract import (
    ToolArguments,
    ToolCall,
    ToolExecutionError,
    ToolResultStatus,
)
from omemo_content_factory.tools.text_metrics import TextMetrics
from omemo_content_factory.tools.toolbox import Toolbox

_TOOL = TextMetrics()


def _measure(text: str) -> dict[str, object]:
    return dict(_TOOL.invoke({"text": text}))


def test_txm_01_counts_characters_words_sentences_and_paragraphs() -> None:
    text = "Первый абзац. Второе предложение!\n\nВторой абзац без точки"
    assert _measure(text) == {
        "characters": len(text),
        "words": 8,
        "sentences": 3,
        "paragraphs": 2,
    }


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("3.5 кг — норма.", 1),
        ("Версия v1.2 вышла", 1),
        ("Что?! Да.", 2),
        ("Без точки в конце", 1),
        ("...", 0),
        ("Раз… Два", 2),
        ("Раз.Два.", 1),
    ],
)
def test_txm_02_a_sentence_ends_at_punctuation_before_whitespace_or_the_end(
    text: str, sentences: int
) -> None:
    assert _measure(text)["sentences"] == sentences


@pytest.mark.parametrize(
    ("text", "paragraphs"),
    [
        ("Один\n\nДва", 2),
        ("Один\n   \nДва", 2),
        ("Один\nвсё ещё один", 1),
        ("\n\nОдин\n\n\n", 1),
    ],
)
def test_txm_03_paragraphs_are_separated_by_a_blank_line(text: str, paragraphs: int) -> None:
    assert _measure(text)["paragraphs"] == paragraphs


@pytest.mark.parametrize("text", ["", "   \n\t "])
def test_txm_04_empty_text_has_no_words_sentences_or_paragraphs(text: str) -> None:
    metrics = _measure(text)
    assert (metrics["words"], metrics["sentences"], metrics["paragraphs"]) == (0, 0, 0)
    assert metrics["characters"] == len(text)


@pytest.mark.parametrize(
    ("limit", "within", "over_by"),
    [(5, True, 0), (6, True, 0), (3, False, 2)],
)
def test_txm_05_checks_the_text_against_an_optional_limit(
    limit: int, within: bool, over_by: int
) -> None:
    metrics = _TOOL.invoke({"text": "abcde", "max_chars": limit})
    assert metrics["max_chars"] == limit
    assert metrics["within_limit"] is within
    assert metrics["over_by"] == over_by


def test_txm_06_without_a_limit_no_limit_keys_are_reported() -> None:
    assert set(_measure("abc")) == {"characters", "words", "sentences", "paragraphs"}


@pytest.mark.parametrize("limit", [0, -3])
def test_txm_07_a_limit_below_one_is_a_tool_failure(limit: int) -> None:
    with pytest.raises(ToolExecutionError, match="max_chars"):
        _TOOL.invoke({"text": "abc", "max_chars": limit})

    result = Toolbox(grants=("text_metrics@v1",), available=[_TOOL]).invoke(
        ToolCall(name="text_metrics", arguments={"text": "abc", "max_chars": limit})
    )
    assert result.status is ToolResultStatus.FAILED
    assert "max_chars" in result.error


def test_txm_07_called_directly_without_text_is_a_tool_failure() -> None:
    with pytest.raises(ToolExecutionError, match="text"):
        _TOOL.invoke({})


def test_txm_08_repeated_calls_give_equal_results() -> None:
    arguments: ToolArguments = {"text": "Раз. Два.", "max_chars": 4}
    assert _TOOL.invoke(arguments) == TextMetrics().invoke(arguments)
