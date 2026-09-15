"""Tests for the Tool descriptor, contract, catalogue and layer boundaries (ADR-0022).

Maps TOOL_ACCEPTANCE.md §1 (TLC) and §2 (TLB). The boundary checks read the layer's source, so a
Tool that starts importing an agent, ``Run``, a Skill, an SDK, the network or the system clock fails
here — not in review.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import pkgutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import omemo_content_factory.tools as tools_pkg
from omemo_content_factory.domain.tool import (
    InvalidToolDescriptorError,
    ToolDescriptor,
    ToolParameter,
    ToolParameterType,
    ToolVersion,
)
from omemo_content_factory.skills.catalogue import SKILL_DESCRIPTORS
from omemo_content_factory.skills.normalize_terminology import NormalizeTerminology
from omemo_content_factory.skills.required_elements import CheckRequiredElements
from omemo_content_factory.skills.segment_text import SegmentText
from omemo_content_factory.tools import current_date, text_metrics
from omemo_content_factory.tools.catalogue import TOOL_DESCRIPTORS, TOOLS_BY_REF
from omemo_content_factory.tools.contract import Tool, ToolCall, ToolResult, ToolResultStatus
from omemo_content_factory.tools.current_date import CurrentDate
from omemo_content_factory.tools.text_metrics import TextMetrics


def _fixed_clock() -> datetime:
    return datetime(2026, 9, 15, 9, 30, tzinfo=UTC)


# Static conformance: mypy --strict rejects these assignments if a Tool drifts from the Protocol.
_CURRENT_DATE: Tool = CurrentDate(clock=_fixed_clock)
_TEXT_METRICS: Tool = TextMetrics()

_LIBRARY: list[tuple[Tool, Any]] = [(_CURRENT_DATE, current_date), (_TEXT_METRICS, text_metrics)]

_TOOLS_SRC = Path(tools_pkg.__file__).parent
_ALLOWED_PROJECT_IMPORTS = {"omemo_content_factory.domain.tool"}
_ALLOWED_STDLIB = {
    "__future__",
    "collections",
    "collections.abc",
    "dataclasses",
    "datetime",
    "enum",
    "re",
    "types",
    "typing",
}
_CLOCK_READS = {"now", "today", "utcnow"}


def _parameter(**overrides: Any) -> ToolParameter:
    fields: dict[str, Any] = {
        "name": "text",
        "kind": ToolParameterType.STRING,
        "description": "Some text.",
    }
    fields.update(overrides)
    return ToolParameter(**fields)


def _descriptor(**overrides: Any) -> ToolDescriptor:
    fields: dict[str, Any] = {
        "tool_id": "demo_tool",
        "version": ToolVersion(1),
        "description": "Do one thing for the model.",
        "parameters": (_parameter(),),
    }
    fields.update(overrides)
    return ToolDescriptor(**fields)


def _source_trees() -> list[tuple[Path, ast.Module]]:
    return [
        (source, ast.parse(source.read_text(encoding="utf-8")))
        for source in sorted(_TOOLS_SRC.glob("*.py"))
    ]


def _imported_modules(tree: ast.Module) -> set[str]:
    """Every absolute module a source file imports (relative imports are resolved as ``tools``)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module if node.level == 0 and node.module else tools_pkg.__name__)
    return found


# --- 1. Descriptor + contract (TLC) ------------------------------------------------------


def test_tlc_01_descriptor_ref_is_id_at_version() -> None:
    assert _descriptor(version=ToolVersion(2)).ref == "demo_tool@v2"


@pytest.mark.parametrize(
    "overrides",
    [
        {"tool_id": ""},
        {"tool_id": "Demo"},
        {"tool_id": "two words"},
        {"tool_id": "demo@v1"},
        {"tool_id": "demo-tool"},
        {"tool_id": "1demo"},
        {"tool_id": "a" * 65},
        {"description": " "},
        {"parameters": [_parameter()]},
        {"parameters": (_parameter(), _parameter())},
        {"parameters": ("text",)},
    ],
)
def test_tlc_02_ill_formed_descriptor_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(InvalidToolDescriptorError):
        _descriptor(**overrides)


@pytest.mark.parametrize("value", [0, -1, True, "1", 1.0])
def test_tlc_02_ill_formed_version_is_refused(value: Any) -> None:
    with pytest.raises(InvalidToolDescriptorError):
        ToolVersion(value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "Text"},
        {"name": "max chars"},
        {"description": ""},
        {"kind": "string"},
        {"required": "yes"},
    ],
)
def test_tlc_02_ill_formed_parameter_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(InvalidToolDescriptorError):
        _parameter(**overrides)


def test_tlc_02_a_tool_may_declare_no_parameters() -> None:
    assert _descriptor(parameters=()).parameters == ()


def test_tlc_03_descriptor_parameter_call_and_result_are_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _descriptor().description = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        _parameter().required = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ToolCall(name="demo_tool").name = "other"  # type: ignore[misc]
    result = ToolResult.ok({"a": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.error = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        cast(Any, result.data)["a"] = 2


@pytest.mark.parametrize(("tool", "module"), _LIBRARY)
def test_tlc_04_each_tool_exposes_its_module_descriptor_and_no_ad_hoc_state(
    tool: Tool, module: Any
) -> None:
    assert tool.descriptor is module.DESCRIPTOR
    assert not hasattr(tool, "__dict__")


def test_tlc_05_catalogue_lists_every_library_tool_once() -> None:
    refs = [d.ref for d in TOOL_DESCRIPTORS]
    assert len(refs) == len(set(refs))
    assert set(TOOLS_BY_REF) == set(refs)
    assert {tool.descriptor for tool, _ in _LIBRARY} == set(TOOL_DESCRIPTORS)
    assert set(refs) == {"current_date@v1", "text_metrics@v1"}


@pytest.mark.parametrize(
    ("status", "data", "error"),
    [
        (ToolResultStatus.OK, {}, "boom"),
        (ToolResultStatus.REFUSED, {}, ""),
        (ToolResultStatus.FAILED, {}, "  "),
        (ToolResultStatus.REFUSED, {"a": 1}, "why"),
        (ToolResultStatus.FAILED, {"a": 1}, "why"),
    ],
)
def test_tlc_06_inconsistent_result_is_refused(
    status: ToolResultStatus, data: dict[str, Any], error: str
) -> None:
    with pytest.raises(ValueError):
        ToolResult(status, data, error)


def test_tlc_06_ok_result_holds_a_copy_of_the_tool_data() -> None:
    source: dict[str, str | int | bool] = {"a": 1}
    result = ToolResult.ok(source)
    source["a"] = 2
    assert result.data == {"a": 1}
    assert result.status is ToolResultStatus.OK
    assert result.error == ""


# --- 2. Boundaries (TLB) -----------------------------------------------------------------


def test_tlb_01_layer_imports_only_pure_stdlib_and_the_tool_descriptor() -> None:
    trees = _source_trees()
    assert len(trees) >= 6  # sanity: the scan sees the package
    offenders: dict[str, set[str]] = {}
    for source, tree in trees:
        bad = {
            module
            for module in _imported_modules(tree)
            if module not in _ALLOWED_PROJECT_IMPORTS
            and module not in _ALLOWED_STDLIB
            and module != tools_pkg.__name__
            and not module.startswith(f"{tools_pkg.__name__}.")
        }
        if bad:
            offenders[source.name] = bad
    assert offenders == {}


def test_tlb_02_no_tool_reads_the_system_clock() -> None:
    offenders = {
        f"{source.name}:{node.lineno}"
        for source, tree in _source_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in _CLOCK_READS
    }
    assert offenders == set()


@pytest.mark.parametrize(("tool", "module"), _LIBRARY)
def test_tlb_03_invoke_takes_only_the_arguments(tool: Tool, module: Any) -> None:
    parameters = list(inspect.signature(tool.invoke).parameters.values())
    assert [p.name for p in parameters] == ["arguments"]
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_ONLY


def test_tlb_04_skill_and_tool_stay_distinct() -> None:
    for tool, _ in _LIBRARY:
        assert not hasattr(tool, "apply")
    for skill in (SegmentText(), NormalizeTerminology(), CheckRequiredElements()):
        assert not hasattr(skill, "invoke")
    skill_ids = {d.skill_id for d in SKILL_DESCRIPTORS}
    tool_ids = {d.tool_id for d in TOOL_DESCRIPTORS}
    assert skill_ids.isdisjoint(tool_ids)


def test_tlb_05_every_layer_module_is_scanned_and_imports_on_its_own() -> None:
    on_disk = {p.stem for p in _TOOLS_SRC.glob("*.py")} - {"__init__"}
    importable = {info.name for info in pkgutil.iter_modules(tools_pkg.__path__)}
    assert on_disk == importable
    for name in importable:
        importlib.import_module(f"{tools_pkg.__name__}.{name}")
