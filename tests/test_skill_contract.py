"""Tests for the Skill contract, descriptor, catalogue and library boundaries (ADR-0021).

Maps SKILL_ACCEPTANCE.md §1 (SKC) and §2 (SKB). The boundary checks read the library's source, so
a Skill that starts importing an agent, ``Run``, the application layer, a model client, the clock or
randomness fails here — not in review.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Any

import pytest

import omemo_content_factory.skills as skills_pkg
from omemo_content_factory.domain.skill import (
    InvalidSkillDescriptorError,
    SkillDescriptor,
    SkillVersion,
)
from omemo_content_factory.skills import normalize_terminology, required_elements, segment_text
from omemo_content_factory.skills.catalogue import SKILL_DESCRIPTORS, SKILLS_BY_REF
from omemo_content_factory.skills.contract import Skill
from omemo_content_factory.skills.normalize_terminology import (
    NormalizedText,
    NormalizeTerminology,
    NormalizeTerminologyInput,
    TermMapping,
)
from omemo_content_factory.skills.required_elements import (
    CheckRequiredElements,
    CheckRequiredElementsInput,
    RequiredElement,
    RequiredElementsReport,
)
from omemo_content_factory.skills.segment_text import (
    SegmentText,
    SegmentTextInput,
    SegmentTextOutput,
)

# Static conformance: mypy --strict rejects these assignments if a Skill drifts from the Protocol.
_SEGMENT: Skill[SegmentTextInput, SegmentTextOutput] = SegmentText()
_NORMALIZE: Skill[NormalizeTerminologyInput, NormalizedText] = NormalizeTerminology()
_CHECK: Skill[CheckRequiredElementsInput, RequiredElementsReport] = CheckRequiredElements()

_LIBRARY: list[tuple[Any, Any]] = [
    (_SEGMENT, SegmentTextInput(text="Раз. Два.", max_chars=5)),
    (
        _NORMALIZE,
        NormalizeTerminologyInput(
            text="омемо", glossary=(TermMapping(variant="омемо", canonical="OMEMO"),)
        ),
    ),
    (
        _CHECK,
        CheckRequiredElementsInput(
            text="Проконсультируйтесь с врачом.",
            elements=(RequiredElement(element_id="doctor", phrases=("с врачом",)),),
        ),
    ),
]

_SKILLS_SRC = Path(skills_pkg.__file__).parent
_ALLOWED_PROJECT_IMPORTS = {"omemo_content_factory.domain.skill"}
_ALLOWED_STDLIB = {
    "__future__",
    "collections",
    "collections.abc",
    "dataclasses",
    "decimal",
    "enum",
    "functools",
    "itertools",
    "math",
    "re",
    "string",
    "typing",
    "unicodedata",
}


def _descriptor(**overrides: Any) -> SkillDescriptor:
    fields: dict[str, Any] = {
        "skill_id": "demo_skill",
        "version": SkillVersion(1),
        "name": "Demo",
        "purpose": "Do one thing.",
    }
    fields.update(overrides)
    return SkillDescriptor(**fields)


def _imported_modules(source: Path) -> set[str]:
    """Every absolute module a source file imports (relative imports are resolved as ``skills``)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module if node.level == 0 and node.module else skills_pkg.__name__)
    return found


# --- 1. Descriptor + contract (SKC) ------------------------------------------------------


def test_skc_01_descriptor_ref_is_id_at_version() -> None:
    descriptor = _descriptor(version=SkillVersion(3))
    assert descriptor.ref == "demo_skill@v3"


@pytest.mark.parametrize(
    "overrides",
    [
        {"skill_id": ""},
        {"skill_id": "  "},
        {"skill_id": "two words"},
        {"skill_id": "demo@v1"},
        {"name": " "},
        {"purpose": ""},
    ],
)
def test_skc_02_ill_formed_descriptor_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(InvalidSkillDescriptorError):
        _descriptor(**overrides)


@pytest.mark.parametrize("value", [0, -1, True, "1", 1.0])
def test_skc_02_ill_formed_version_is_refused(value: Any) -> None:
    with pytest.raises(InvalidSkillDescriptorError):
        SkillVersion(value)


def test_skc_03_descriptor_inputs_and_outputs_are_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _descriptor().name = "changed"  # type: ignore[misc]
    for skill, skill_input in _LIBRARY:
        output = skill.apply(skill_input)
        first_field = dataclasses.fields(output)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(output, first_field, None)
        with pytest.raises(dataclasses.FrozenInstanceError):
            skill_input.text = "changed"


@pytest.mark.parametrize(
    ("skill", "module"),
    [(_SEGMENT, segment_text), (_NORMALIZE, normalize_terminology), (_CHECK, required_elements)],
)
def test_skc_04_each_skill_exposes_its_module_descriptor_and_holds_no_state(
    skill: Any, module: Any
) -> None:
    assert skill.descriptor is module.DESCRIPTOR
    assert type(skill).__slots__ == ()
    assert not hasattr(skill, "__dict__")


def test_skc_05_catalogue_lists_every_library_skill_once() -> None:
    refs = [d.ref for d in SKILL_DESCRIPTORS]
    assert len(refs) == len(set(refs))
    assert set(SKILLS_BY_REF) == set(refs)
    assert {skill.descriptor for skill, _ in _LIBRARY} == set(SKILL_DESCRIPTORS)
    assert set(refs) == {
        "segment_text@v1",
        "normalize_terminology@v1",
        "check_required_elements@v1",
    }


# --- 2. Boundaries (SKB) -----------------------------------------------------------------


def test_skb_01_library_imports_only_pure_stdlib_and_the_skill_descriptor() -> None:
    sources = sorted(_SKILLS_SRC.glob("*.py"))
    assert len(sources) >= 5  # sanity: the scan sees the package
    offenders: dict[str, set[str]] = {}
    for source in sources:
        bad = {
            module
            for module in _imported_modules(source)
            if module not in _ALLOWED_PROJECT_IMPORTS
            and module not in _ALLOWED_STDLIB
            and module != skills_pkg.__name__
            and not module.startswith(f"{skills_pkg.__name__}.")
        }
        if bad:
            offenders[source.name] = bad
    assert offenders == {}


def test_skb_01_every_library_module_is_scanned() -> None:
    on_disk = {p.stem for p in _SKILLS_SRC.glob("*.py")} - {"__init__"}
    importable = {info.name for info in pkgutil.iter_modules(skills_pkg.__path__)}
    assert on_disk == importable
    for name in importable:  # every module imports cleanly on its own
        importlib.import_module(f"{skills_pkg.__name__}.{name}")


@pytest.mark.parametrize(("skill", "skill_input"), _LIBRARY)
def test_skb_02_apply_takes_only_the_typed_input(skill: Any, skill_input: Any) -> None:
    parameters = list(inspect.signature(skill.apply).parameters.values())
    assert [p.name for p in parameters] == ["skill_input"]
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_ONLY


@pytest.mark.parametrize(("skill", "skill_input"), _LIBRARY)
def test_skb_03_apply_is_deterministic_across_calls_and_instances(
    skill: Any, skill_input: Any
) -> None:
    first = skill.apply(skill_input)
    assert skill.apply(skill_input) == first
    assert type(skill)().apply(skill_input) == first
