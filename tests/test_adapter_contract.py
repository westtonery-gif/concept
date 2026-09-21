"""Tests for the Adapter Layer contracts and the core's boundary to the outside world (ADR-0023).

Maps ADAPTER_ACCEPTANCE.md §1 (ADC) and §2 (ADB). The contracts are checked by shape, by their
values, and by minimal conformers that mypy --strict holds to the Protocols; each implementation
has its own tests (§5 STO, §6 STB). The boundary checks read the source tree: a core module that
starts importing a vendor SDK, or a contract that starts importing an implementation, fails here —
not in review.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import pkgutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import omemo_content_factory as root_pkg
import omemo_content_factory.adapters as adapters_pkg
from omemo_content_factory.adapters.analytics_sink import AnalyticsSink
from omemo_content_factory.adapters.brief_board import BriefBoard, IncomingBrief
from omemo_content_factory.adapters.review_desk import ReviewDecision, ReviewDesk, ReviewPackage
from omemo_content_factory.adapters.run_store import RunIndex, RunStore
from omemo_content_factory.domain.analytics import AnalyticsRecord
from omemo_content_factory.domain.artifact import ArtifactStatus, ArtifactView
from omemo_content_factory.domain.human_review import ReviewId, ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunStatus

_PROJECT = root_pkg.__name__
_SRC = Path(root_pkg.__file__).parent
_ADAPTERS_SRC = Path(adapters_pkg.__file__).parent
_ALLOWED_STDLIB = {"__future__", "collections.abc", "dataclasses", "enum", "typing"}
"""``enum`` was added for ``ClipMode`` (ADR-0058 §1): a closed vocabulary on a contract is
data, like a frozen dataclass, and carries no behaviour. The list stays an allowlist — a
module widening it must say why here."""
_IO_STDLIB = {
    "dbm",
    "ftplib",
    "http",
    "shelve",
    "smtplib",
    "socket",
    "sqlite3",
    "ssl",
    "subprocess",
    "urllib",
}
_VENDOR_WORDS = ("notion", "google", "gdoc", "anthropic", "claude", "n8n", "sql")


# --- Minimal conformers: mypy --strict rejects these if a Protocol drifts ----------------


class _NullStore:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def save(self, run: Run, /) -> None:
        self.saved.append(run.run_id)

    def load(self, run_id: str, /) -> Run | None:
        return None


class _NullIndex:
    def run_ids(self, /, *, status: RunStatus) -> tuple[str, ...]:
        return ()


class _NullBoard:
    def __init__(self) -> None:
        self.reported: list[tuple[str, str, RunStatus]] = []

    def fetch_brief(self, brief_ref: str, /) -> IncomingBrief | None:
        return IncomingBrief(brief_ref=brief_ref, body="Тема: сон и восстановление.")

    def report_status(self, brief_ref: str, /, *, run_id: str, status: RunStatus) -> None:
        self.reported.append((brief_ref, run_id, status))

    def report_review_location(self, brief_ref: str, /, *, run_id: str, location: str) -> None:
        self.reported.append((brief_ref, run_id, RunStatus.WAITING_HUMAN))


class _NullDesk:
    def publish(self, package: ReviewPackage, /) -> str:
        return f"desk://{package.review_id}"

    def fetch_decision(self, review_id: ReviewId, /) -> ReviewDecision | None:
        return None


class _NullSink:
    def __init__(self) -> None:
        self.exported: list[AnalyticsRecord] = []

    def export(self, records: Sequence[AnalyticsRecord], /) -> None:
        self.exported.extend(records)


_STORE: RunStore = _NullStore()
_INDEX: RunIndex = _NullIndex()
_BOARD: BriefBoard = _NullBoard()
_DESK: ReviewDesk = _NullDesk()
_SINK: AnalyticsSink = _NullSink()


def _apply_decision(run: Run, review_id: ReviewId, decision: ReviewDecision) -> None:
    """ADC-05 (static): a fetched decision goes into the unchanged Run API as it is."""
    run.submit_review(review_id, decision.decision, by=Actor.HUMAN_REVIEWER, reason=decision.reason)


# --- Helpers ------------------------------------------------------------------------------


def _candidate(**overrides: Any) -> ArtifactView:
    fields: dict[str, Any] = {
        "artifact_id": "r-1-artifact-1",
        "run_id": "r-1",
        "output_ref": "r-1-task-1-output-1",
        "kind": "script",
        "content": "Черновик сценария.",
        "version": 1,
        "status": ArtifactStatus.CANDIDATE,
    }
    fields.update(overrides)
    return ArtifactView(**fields)


def _package(**overrides: Any) -> ReviewPackage:
    fields: dict[str, Any] = {
        "run_id": "r-1",
        "review_id": "r-1-review-1",
        "candidate": _candidate(),
        "brief": "Тема: сон и восстановление.",
    }
    fields.update(overrides)
    return ReviewPackage(**fields)


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _absolute_imports(tree: ast.Module) -> set[str]:
    """Every absolute module a source file imports (the codebase uses no relative imports)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _outside_infrastructure() -> list[Path]:
    infrastructure = _SRC / "infrastructure"
    return [p for p in _python_files(_SRC) if infrastructure not in p.parents]


def _is_project(module: str, package: str) -> bool:
    return module == f"{_PROJECT}.{package}" or module.startswith(f"{_PROJECT}.{package}.")


# --- 1. Contracts (ADC) -------------------------------------------------------------------

_POS = "POSITIONAL_ONLY"
_KW = "KEYWORD_ONLY"
_CONTRACT_METHODS: dict[Any, dict[str, list[tuple[str, str]]]] = {
    RunStore: {"save": [("run", _POS)], "load": [("run_id", _POS)]},
    RunIndex: {"run_ids": [("status", _KW)]},
    BriefBoard: {
        "fetch_brief": [("brief_ref", _POS)],
        "report_status": [("brief_ref", _POS), ("run_id", _KW), ("status", _KW)],
        "report_review_location": [("brief_ref", _POS), ("run_id", _KW), ("location", _KW)],
    },
    ReviewDesk: {"publish": [("package", _POS)], "fetch_decision": [("review_id", _POS)]},
    AnalyticsSink: {"export": [("records", _POS)]},
}


@pytest.mark.parametrize(("contract", "methods"), list(_CONTRACT_METHODS.items()))
def test_adc_01_each_contract_has_exactly_its_declared_methods(
    contract: Any, methods: dict[str, list[tuple[str, str]]]
) -> None:
    public = {name for name, value in vars(contract).items() if callable(value) and name[0] != "_"}
    assert public == set(methods)
    for name, expected in methods.items():
        parameters = list(inspect.signature(getattr(contract, name)).parameters.values())[1:]
        assert [(p.name, p.kind.name) for p in parameters] == expected


@pytest.mark.parametrize(
    "overrides",
    [{"brief_ref": ""}, {"brief_ref": "  "}, {"body": ""}, {"body": "\n"}, {"body": None}],
)
def test_adc_02_ill_formed_incoming_brief_is_refused(overrides: dict[str, Any]) -> None:
    fields: dict[str, Any] = {"brief_ref": "brief-7", "body": "Тема."}
    fields.update(overrides)
    with pytest.raises(ValueError):
        IncomingBrief(**fields)


def test_adc_02_incoming_brief_is_immutable() -> None:
    brief = IncomingBrief(brief_ref="brief-7", body="Тема.")
    with pytest.raises(dataclasses.FrozenInstanceError):
        brief.body = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": ""},
        {"review_id": " "},
        {"brief": ""},
        {"candidate": _candidate(run_id="r-2")},
        {"candidate": _candidate(status=ArtifactStatus.DRAFT)},
        {"candidate": _candidate(status=ArtifactStatus.APPROVED)},
        {"qa_flags": ["flag"]},
        {"qa_flags": ("ok", " ")},
    ],
)
def test_adc_03_ill_formed_review_package_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _package(**overrides)


def test_adc_03_review_package_holds_the_context_and_is_immutable() -> None:
    assert _package().qa_flags == ()
    package = _package(qa_flags=("источник не указан",))
    assert package.qa_flags == ("источник не указан",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        package.brief = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("decision", "reason"),
    [(ReviewStatus.PENDING, None), ("approved", None), (ReviewStatus.REJECTED, " ")],
)
def test_adc_04_ill_formed_review_decision_is_refused(decision: Any, reason: Any) -> None:
    with pytest.raises(ValueError):
        ReviewDecision(decision=decision, reason=reason)


@pytest.mark.parametrize(
    "decision",
    [ReviewStatus.APPROVED, ReviewStatus.REJECTED, ReviewStatus.CHANGES_REQUESTED],
)
def test_adc_04_every_terminal_decision_is_accepted(decision: ReviewStatus) -> None:
    fetched = ReviewDecision(decision=decision, reason="Уточнить дозировки.")
    assert fetched.decision is decision
    with pytest.raises(dataclasses.FrozenInstanceError):
        fetched.reason = None  # type: ignore[misc]


def test_adc_05_contracts_fit_the_unchanged_run_api() -> None:
    board, store, sink = _NullBoard(), _NullStore(), _NullSink()
    brief = board.fetch_brief("brief-7")
    assert brief is not None
    run = Run.create(run_id="r-1", content_brief_ref=brief.brief_ref, workflow_version_ref="wf@v1")

    store.save(run)
    board.report_status(brief.brief_ref, run_id=run.run_id, status=run.status)
    sink.export(run.analytics_records)

    assert store.saved == ["r-1"]
    assert board.reported == [("brief-7", "r-1", RunStatus.CREATED)]
    assert sink.exported == []


# --- 2. Boundaries (ADB) ------------------------------------------------------------------


def test_adb_01_contracts_import_only_pure_stdlib_and_the_domain() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _python_files(_ADAPTERS_SRC):
        bad = {
            module
            for module in _absolute_imports(_parse(path))
            if module not in _ALLOWED_STDLIB
            and not _is_project(module, "domain")
            and not _is_project(module, "adapters")
        }
        if bad:
            offenders[path.name] = bad
    assert offenders == {}


def test_adb_02_only_infrastructure_reaches_outside_the_process() -> None:
    files = _outside_infrastructure()
    assert len(files) > 30  # sanity: the scan sees the core
    offenders: dict[str, set[str]] = {}
    for path in files:
        bad = {
            module
            for module in _absolute_imports(_parse(path))
            if (top := module.split(".")[0]) != _PROJECT
            and (top not in sys.stdlib_module_names or top in _IO_STDLIB)
        }
        if bad:
            offenders[str(path.relative_to(_SRC))] = bad
    assert offenders == {}


def test_adb_03_only_the_composition_root_imports_infrastructure() -> None:
    offenders = {
        str(path.relative_to(_SRC))
        for path in _outside_infrastructure()
        if path != _SRC / "composition.py"
        and any(_is_project(m, "infrastructure") for m in _absolute_imports(_parse(path)))
    }
    assert offenders == set()


def test_adb_04_the_domain_imports_only_itself() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _python_files(_SRC / "domain"):
        bad = {
            module
            for module in _absolute_imports(_parse(path))
            if module.split(".")[0] == _PROJECT and not _is_project(module, "domain")
        }
        if bad:
            offenders[path.name] = bad
    assert offenders == {}


def test_adb_05_contracts_name_no_vendor() -> None:
    names: set[str] = set()
    for path in _python_files(_ADAPTERS_SRC):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ClassDef | ast.FunctionDef):
                names.add(node.name)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.Name):
                names.add(node.id)
    assert {n for n in names if any(word in n.lower() for word in _VENDOR_WORDS)} == set()


def test_adb_06_every_contract_module_is_scanned_and_imports_on_its_own() -> None:
    on_disk = {p.stem for p in _ADAPTERS_SRC.glob("*.py")} - {"__init__"}
    importable = {info.name for info in pkgutil.iter_modules(adapters_pkg.__path__)}
    assert (
        on_disk
        == importable
        == {
            "analytics_sink",
            "brief_board",
            "clip_renderer",
            "episode_board",
            "episode_source",
            "footage_index",
            "image_generator",
            "review_desk",
            "run_store",
            "video_generator",
        }
    )
    for name in importable:
        importlib.import_module(f"{adapters_pkg.__name__}.{name}")
