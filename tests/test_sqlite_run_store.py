"""Tests for the SQLite Storage Adapter and its snapshot codec (ADR-0024).

Maps ADAPTER_ACCEPTANCE.md §5 (STO). Every test works on a real database file under pytest's
``tmp_path``. A "restart" is a second store object on the same file, sharing nothing with the first
but the file. Stored rows are read and tampered with through ``sqlite3`` directly, the way a broken
disk or another release would leave them. No mocks, sleep or randomness.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import json
import pkgutil
import sqlite3
import typing
from collections.abc import Callable
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import omemo_content_factory.domain as domain_pkg
from omemo_content_factory.adapters.run_store import RunStore, RunStoreError
from omemo_content_factory.domain.analytics import InvalidAnalyticsRecordError
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import (
    Run,
    RunEvent,
    RunLogEvent,
    RunRestorationError,
    RunStatus,
)
from omemo_content_factory.infrastructure.run_snapshot_codec import (
    EVENT_TYPES,
    FORMAT_VERSION,
    SnapshotFormatError,
    decode_snapshot,
    encode_snapshot,
)
from omemo_content_factory.infrastructure.sqlite_run_store import SqliteRunStore
from tests.restorable_runs import (
    CD,
    REVIEWER,
    RUN_ID,
    V2,
    V2_REVIEW,
    busy_run,
    succeeded_task,
)

Document = dict[str, Any]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "runs.sqlite3"


def stored_rows(db: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(db)) as connection:
        rows: list[tuple[str, str]] = connection.execute(
            "SELECT run_id, snapshot FROM runs"
        ).fetchall()
    return rows


def write_row(db: Path, run_id: str, text: str) -> None:
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute("INSERT OR REPLACE INTO runs VALUES (?, ?)", (run_id, text))


def stored_busy_run(db: Path, edit: Callable[[Document], object]) -> None:
    """Store :func:`busy_run`, then apply ``edit`` to its stored document in place."""
    SqliteRunStore(db).save(busy_run())
    [(run_id, text)] = stored_rows(db)
    document = json.loads(text)
    edit(document)
    write_row(db, run_id, json.dumps(document))


def set_run(field: str, value: object) -> Callable[[Document], None]:
    def edit(document: Document) -> None:
        document["run"][field] = value

    return edit


def set_cost(amount: object) -> Callable[[Document], None]:
    def edit(document: Document) -> None:
        document["run"]["analytics_records"][0]["cost"]["amount"] = amount

    return edit


# --- Storing and loading ------------------------------------------------------------------


def test_sto_01_a_saved_run_survives_a_restart(db: Path) -> None:
    run = busy_run()
    store: RunStore = SqliteRunStore(db)
    store.save(run)

    back = SqliteRunStore(db).load(RUN_ID)

    assert back is not None
    assert back.snapshot == run.snapshot
    record = back.analytics_records[1]
    assert record.cost.amount == Decimal("0.0123")
    assert record.time_range.started_at.utcoffset() == timedelta(hours=3)


def test_sto_02_an_unknown_run_loads_as_none(db: Path) -> None:
    store = SqliteRunStore(db)
    assert store.load(RUN_ID) is None

    store.save(busy_run())

    assert store.load("run-unknown") is None


def test_sto_03_save_replaces_the_stored_truth(db: Path) -> None:
    store = SqliteRunStore(db)
    run = busy_run()
    store.save(run)
    run.submit_review(V2_REVIEW, ReviewStatus.APPROVED, by=REVIEWER)
    run.transition_artifact(V2, ArtifactStatus.APPROVED, by=CD)
    run.transition(RunStatus.COMPLETED, by=CD)

    store.save(run)

    back = store.load(RUN_ID)
    assert back is not None
    assert back.status is RunStatus.COMPLETED
    assert back.snapshot == run.snapshot
    assert len(stored_rows(db)) == 1


def test_sto_04_saving_again_without_changes_changes_nothing(db: Path) -> None:
    store = SqliteRunStore(db)
    run = busy_run()
    store.save(run)
    first = stored_rows(db)

    store.save(run)
    back = store.load(RUN_ID)
    assert back is not None
    store.save(back)

    assert stored_rows(db) == first


def test_sto_05_a_loaded_run_continues_and_is_saved_again(db: Path) -> None:
    store = SqliteRunStore(db)
    store.save(busy_run())
    back = store.load(RUN_ID)
    assert back is not None

    task_id, _ = succeeded_task(back, step="edit", payload="Правка.")
    store.save(back)
    again = SqliteRunStore(db).load(RUN_ID)

    assert task_id == f"{RUN_ID}-task-6"
    assert again is not None
    assert again.snapshot == back.snapshot
    assert again.open_task("publish", "publisher@v1", "бриф", by=CD) == f"{RUN_ID}-task-7"


# --- Failures -----------------------------------------------------------------------------


def test_sto_06_a_failed_save_leaves_the_previous_truth_intact(db: Path) -> None:
    run = busy_run()
    SqliteRunStore(db).save(run)
    saved = run.snapshot
    run.transition(RunStatus.FAILED, by=CD, reason="сбой")
    locker = sqlite3.connect(db, isolation_level=None)
    locker.execute("BEGIN EXCLUSIVE")
    blocked = SqliteRunStore(db, timeout=0)

    try:
        with pytest.raises(RunStoreError, match="could not be saved"):
            blocked.save(run)
        with pytest.raises(RunStoreError, match="could not be read"):
            blocked.load(RUN_ID)
    finally:
        locker.execute("ROLLBACK")
        locker.close()

    back = SqliteRunStore(db).load(RUN_ID)
    assert back is not None
    assert back.snapshot == saved


def test_sto_07_a_store_that_cannot_be_opened_raises_run_store_error(tmp_path: Path) -> None:
    not_a_database = tmp_path / "junk.sqlite3"
    not_a_database.write_bytes(b"this is not an SQLite database, only some bytes" * 4)

    for path in (tmp_path, tmp_path / "missing" / "runs.sqlite3", not_a_database):
        store = SqliteRunStore(path)
        with pytest.raises(RunStoreError):
            store.save(busy_run())
        with pytest.raises(RunStoreError):
            store.load(RUN_ID)


def _drop_task_seq(document: Document) -> None:
    del document["run"]["task_seq"]


def _unknown_event(document: Document) -> None:
    document["run"]["events"][0]["type"] = "RunPaused"


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda d: d.update(format=FORMAT_VERSION + 1), id="newer-format"),
        pytest.param(lambda d: d.update(format=True), id="bool-format"),
        pytest.param(lambda d: d.update(extra=1), id="extra-top-level-key"),
        pytest.param(_drop_task_seq, id="missing-field"),
        pytest.param(set_run("surplus", 1), id="unknown-field"),
        pytest.param(set_run("task_seq", "5"), id="string-for-int"),
        pytest.param(set_run("rework_count", True), id="bool-for-int"),
        pytest.param(set_run("status", "paused"), id="unknown-status"),
        pytest.param(set_run("tasks", {}), id="object-for-list"),
        pytest.param(_unknown_event, id="unknown-event-type"),
        pytest.param(set_cost("не число"), id="bad-decimal"),
        pytest.param(set_cost(0.5), id="float-cost"),
    ],
)
def test_sto_08_a_malformed_stored_document_raises_run_store_error(
    db: Path, edit: Callable[[Document], object]
) -> None:
    stored_busy_run(db, edit)

    with pytest.raises(RunStoreError, match="malformed"):
        SqliteRunStore(db).load(RUN_ID)


def test_sto_08_unreadable_text_or_a_row_of_another_run_raises_run_store_error(
    db: Path,
) -> None:
    store = SqliteRunStore(db)
    store.save(busy_run())
    [(_, text)] = stored_rows(db)
    write_row(db, "run-copy", text)
    write_row(db, RUN_ID, "{not json")

    with pytest.raises(RunStoreError, match="malformed"):
        store.load(RUN_ID)
    with pytest.raises(RunStoreError, match="holds run"):
        store.load("run-copy")


def test_sto_09_a_refused_restoration_passes_through_unmasked(db: Path) -> None:
    stored_busy_run(db, set_run("task_seq", 1))

    with pytest.raises(RunRestorationError):
        SqliteRunStore(db).load(RUN_ID)


def test_sto_09_a_value_the_domain_refuses_passes_through_unmasked(db: Path) -> None:
    stored_busy_run(db, set_cost("-1"))

    with pytest.raises(InvalidAnalyticsRecordError):
        SqliteRunStore(db).load(RUN_ID)


# --- The codec ----------------------------------------------------------------------------


def test_sto_10_the_codec_round_trips_a_snapshot_through_json_text() -> None:
    snapshot = busy_run().snapshot

    document = json.loads(json.dumps(encode_snapshot(snapshot)))

    assert document["format"] == FORMAT_VERSION
    assert decode_snapshot(document) == snapshot


def _journal_event_classes() -> set[type[Any]]:
    """Every concrete journal event class defined in the domain."""
    bases = typing.get_args(RunLogEvent)
    found: set[type[Any]] = set()
    for info in pkgutil.iter_modules(domain_pkg.__path__):
        module = importlib.import_module(f"{domain_pkg.__name__}.{info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, bases) and cls not in bases and cls.__module__ == module.__name__:
                found.add(cls)
    return found


def test_sto_11_every_journal_event_class_is_registered() -> None:
    assert set(EVENT_TYPES.values()) == _journal_event_classes()
    assert all(EVENT_TYPES[cls.__name__] is cls for cls in _journal_event_classes())


@dataclasses.dataclass(frozen=True, slots=True)
class _StrayEvent(RunEvent):
    """A journal event the codec was never told about."""


def test_sto_11_an_unregistered_event_fails_the_save_not_the_load(db: Path) -> None:
    snapshot = busy_run().snapshot
    stray = Run.restore(
        dataclasses.replace(snapshot, events=(*snapshot.events, _StrayEvent(run_id=RUN_ID)))
    )

    with pytest.raises(SnapshotFormatError, match="_StrayEvent"):
        encode_snapshot(stray.snapshot)
    with pytest.raises(RunStoreError, match="could not be encoded"):
        SqliteRunStore(db).save(stray)
    assert SqliteRunStore(db).load(RUN_ID) is None
