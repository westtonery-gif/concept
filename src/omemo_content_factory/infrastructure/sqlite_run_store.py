"""SQLite ``RunStore``: the MVP Storage Adapter (ADR-0024).

Implements the ``RunStore`` contract (ADR-0023 §5, ADAPTER_SPEC §4) on the embedded database that
ships with Python. PROJECT.md §5 asks for a simple file or embedded store for the MVP. Each Run is
one row: its ``run_id`` and its whole truth, a JSON document of its ``RunSnapshot``
(``run_snapshot_codec``). With one row per aggregate, a save is one statement in one transaction.
It is atomic, and a failed save leaves the previous truth as it was.

- ``save`` replaces the row under the Run's id. An unchanged Run is written as the same document,
  so repeating a save changes nothing.
- ``load`` returns ``None`` for an unknown id, otherwise ``Run.restore`` of the stored snapshot. A
  snapshot the Run refuses raises ``RunRestorationError`` unmasked, as does any other domain error
  raised while the snapshot is rebuilt.
- A database that cannot be opened, read or written, and a stored row that is not a well-formed
  document, raise ``RunStoreError``.

Each call opens and closes its own connection, so the store holds no handle between calls.
Concurrent writers, listing stored Runs and format migrations are deferred (ADR-0024 "Deferred").
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from omemo_content_factory.adapters.run_store import RunStoreError
from omemo_content_factory.domain.run import Run
from omemo_content_factory.infrastructure.run_snapshot_codec import (
    SnapshotFormatError,
    decode_snapshot,
    encode_snapshot,
)

_CREATE_TABLE = "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)"
_UPSERT = "INSERT OR REPLACE INTO runs (run_id, snapshot) VALUES (?, ?)"
_SELECT = "SELECT snapshot FROM runs WHERE run_id = ?"


class SqliteRunStore:
    """A ``RunStore`` keeping each Run's whole truth as one row of an SQLite database file.

    ``path`` is the database file, created on first use (its directory must exist). ``timeout`` is
    how long, in seconds, a call waits for another connection's lock before failing.
    """

    def __init__(self, path: str | Path, *, timeout: float = 5.0) -> None:
        self._path = Path(path)
        self._timeout = timeout

    def save(self, run: Run, /) -> None:
        """Store the Run's whole current truth in one transaction, replacing its previous row."""
        try:
            document = json.dumps(encode_snapshot(run.snapshot), ensure_ascii=False)
        except SnapshotFormatError as error:
            raise RunStoreError(f"run {run.run_id} could not be encoded: {error}") from error
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_UPSERT, (run.run_id, document))
        except sqlite3.Error as error:
            raise RunStoreError(f"run {run.run_id} could not be saved: {error}") from error

    def load(self, run_id: str, /) -> Run | None:
        """Bring the stored Run back, or ``None`` if nothing is stored under ``run_id``."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(_SELECT, (run_id,)).fetchone()
        except sqlite3.Error as error:
            raise RunStoreError(f"run {run_id} could not be read: {error}") from error
        if row is None:
            return None
        try:
            snapshot = decode_snapshot(json.loads(row[0]))
        except (TypeError, ValueError) as error:
            raise RunStoreError(f"run {run_id} is stored in a malformed form: {error}") from error
        if snapshot.run_id != run_id:
            raise RunStoreError(f"the row of run {run_id} holds run {snapshot.run_id}")
        return Run.restore(snapshot)

    def _connect(self) -> sqlite3.Connection:
        """Open the database and make sure its one table exists."""
        connection = sqlite3.connect(self._path, timeout=self._timeout)
        try:
            connection.execute(_CREATE_TABLE)
        except sqlite3.Error:
            connection.close()
            raise
        return connection
