"""Tests for ``normalize_terminology@v1`` (ADR-0021 §4; SKILL_ACCEPTANCE.md §4, TRM)."""

from __future__ import annotations

from typing import Any

import pytest

from omemo_content_factory.domain.skill import InvalidSkillInputError
from omemo_content_factory.skills.normalize_terminology import (
    NormalizedText,
    NormalizeTerminology,
    NormalizeTerminologyInput,
    TermMapping,
    TermReplacement,
)

_SKILL = NormalizeTerminology()


def _normalize(text: str, *pairs: tuple[str, str]) -> NormalizedText:
    glossary = tuple(TermMapping(variant=v, canonical=c) for v, c in pairs)
    return _SKILL.apply(NormalizeTerminologyInput(text=text, glossary=glossary))


def test_trm_01_variant_is_replaced_case_insensitively() -> None:
    result = _normalize("Приложение омемо и Омемо.", ("омемо", "OMEMO"))
    assert result.text == "Приложение OMEMO и OMEMO."
    assert result.replacements == (
        TermReplacement(variant="омемо", canonical="OMEMO", occurrences=2),
    )


def test_trm_02_only_whole_words_match() -> None:
    result = _normalize("кот и котлета", ("кот", "кошка"))
    assert result.text == "кошка и котлета"


def test_trm_03_longest_variant_wins_regardless_of_glossary_order() -> None:
    result = _normalize(
        "СД 2 и СД",
        ("СД", "сахарный диабет"),
        ("СД 2", "сахарный диабет 2 типа"),
    )
    assert result.text == "сахарный диабет 2 типа и сахарный диабет"


def test_trm_04_one_pass_never_re_replaces_a_canonical_term() -> None:
    result = _normalize("A B", ("A", "B"), ("B", "C"))
    assert result.text == "B C"


def test_trm_05_matching_tolerates_any_whitespace_inside_a_variant() -> None:
    result = _normalize("Омега\n3 полезна", ("омега 3", "омега-3"))
    assert result.text == "омега-3 полезна"


def test_trm_06_already_canonical_occurrences_are_not_counted() -> None:
    result = _normalize("OMEMO и omemo", ("omemo", "OMEMO"))
    assert result.text == "OMEMO и OMEMO"
    assert result.replacements[0].occurrences == 1


def test_trm_07_empty_glossary_leaves_text_untouched() -> None:
    result = _normalize("Без изменений.")
    assert result == NormalizedText(text="Без изменений.", replacements=())


def test_trm_09_report_is_in_glossary_order_and_skips_unused_entries() -> None:
    result = _normalize(
        "вит Д и мг",
        ("мг", "миллиграмм"),
        ("ккал", "килокалория"),
        ("вит Д", "витамин D"),
    )
    assert [r.variant for r in result.replacements] == ["мг", "вит Д"]


@pytest.mark.parametrize(
    "pairs",
    [
        (("", "x"),),
        (("x", "  "),),
        (("Омемо", "OMEMO"), ("омемо ", "OMEMO")),
        (("омега 3", "a"), ("Омега   3", "b")),
    ],
)
def test_trm_08_invalid_glossary_is_refused(pairs: tuple[tuple[str, str], ...]) -> None:
    glossary = tuple(TermMapping(variant=v, canonical=c) for v, c in pairs)
    with pytest.raises(InvalidSkillInputError):
        NormalizeTerminologyInput(text="t", glossary=glossary)


def test_trm_08_non_str_text_is_refused() -> None:
    bad: Any = None
    with pytest.raises(InvalidSkillInputError):
        NormalizeTerminologyInput(text=bad, glossary=())
