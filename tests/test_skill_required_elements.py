"""Tests for ``check_required_elements@v1`` (ADR-0021 §4; SKILL_ACCEPTANCE.md §5, REQ)."""

from __future__ import annotations

from typing import Any

import pytest

from omemo_content_factory.domain.skill import InvalidSkillInputError
from omemo_content_factory.skills.required_elements import (
    CheckRequiredElements,
    CheckRequiredElementsInput,
    RequiredElement,
    RequiredElementsReport,
)

_SKILL = CheckRequiredElements()

_DISCLAIMER = RequiredElement(
    element_id="disclaimer",
    phrases=("не является медицинской рекомендацией", "проконсультируйтесь с врачом"),
)
_CTA = RequiredElement(element_id="cta", phrases=("подпишитесь",))


def _check(text: str, *elements: RequiredElement) -> RequiredElementsReport:
    return _SKILL.apply(CheckRequiredElementsInput(text=text, elements=elements))


def test_req_01_all_present_is_complete() -> None:
    report = _check("Подпишитесь! Проконсультируйтесь с врачом.", _DISCLAIMER, _CTA)
    assert report == RequiredElementsReport(present=("disclaimer", "cta"), missing=())
    assert report.complete


def test_req_02_missing_elements_are_reported_in_input_order() -> None:
    report = _check("Просто текст.", _CTA, _DISCLAIMER)
    assert report.missing == ("cta", "disclaimer")
    assert report.present == ()
    assert not report.complete


def test_req_03_any_one_phrase_satisfies_an_element() -> None:
    assert _check("Материал не является медицинской рекомендацией.", _DISCLAIMER).complete


def test_req_04_match_is_case_and_whitespace_insensitive() -> None:
    assert _check("НЕ ЯВЛЯЕТСЯ\nмедицинской   рекомендацией", _DISCLAIMER).complete


def test_req_05_only_whole_words_match() -> None:
    element = RequiredElement(element_id="doctor", phrases=("врач",))
    assert not _check("врачебный осмотр", element).complete
    assert _check("спросите врач-а? нет, врач.", element).complete


def test_req_07_phrases_are_literal_not_regex() -> None:
    adult = RequiredElement(element_id="adult", phrases=("18+",))
    dotted = RequiredElement(element_id="dotted", phrases=("a.b",))
    assert _check("Контент 18+ только", adult).complete
    assert not _check("axb", dotted).complete


@pytest.mark.parametrize(
    "elements",
    [
        (),
        (RequiredElement(element_id=" ", phrases=("x",)),),
        (RequiredElement(element_id="a", phrases=()),),
        (RequiredElement(element_id="a", phrases=("ok", "  ")),),
        (_CTA, RequiredElement(element_id="cta", phrases=("other",))),
    ],
)
def test_req_06_invalid_requirements_are_refused(elements: tuple[RequiredElement, ...]) -> None:
    with pytest.raises(InvalidSkillInputError):
        CheckRequiredElementsInput(text="t", elements=elements)


def test_req_06_non_str_text_is_refused() -> None:
    bad: Any = b"bytes"
    with pytest.raises(InvalidSkillInputError):
        CheckRequiredElementsInput(text=bad, elements=(_CTA,))
