"""The Storage Adapter contract — the truth of a Run outlives the process (ADR-0023 §5).

``RunStore`` persists one whole Run aggregate — its state, children, analytics records and event
journal — and gives it back as the same Run (ADR-0015 I1, I3, I7). How the truth is encoded is the
implementation's concern: callers hand over a ``Run`` and get a ``Run`` back, never a snapshot or a
row (RUN_RESTORE_SPEC §6). Definitions (Workflow, Schema, Agent, Prompt) are not stored here; they
are the Run's dependencies, not its truth (ADR-0015 §2).
"""

from __future__ import annotations

from typing import Protocol

from omemo_content_factory.domain.run import Run


class RunStoreError(Exception):
    """The store could not read or write (technical failure, not a ``DomainError``)."""


class RunStore(Protocol):
    """Durable storage of Run aggregates, one stored truth per ``run_id`` (ADAPTER_SPEC §4)."""

    def save(self, run: Run, /) -> None:
        """Store the Run's whole current truth atomically, replacing what its id held before.

        Saving an unchanged Run again changes nothing. A failed save leaves the previous stored
        truth intact and raises ``RunStoreError``.
        """
        ...

    def load(self, run_id: str, /) -> Run | None:
        """Bring a stored Run back, or ``None`` if nothing is stored under ``run_id``.

        Stored truth that the Run refuses to admit raises the Run's own domain error, unmasked.
        """
        ...
