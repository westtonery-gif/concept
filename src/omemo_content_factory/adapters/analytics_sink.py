"""The Analytics Adapter contract — metrics leave for the analytics store (ADR-0023 §8).

``AnalyticsSink`` receives copies of a Run's Analytics Records. The Run keeps the authoritative
records (ADR-0020) and the Run Store persists them, so the sink is a downstream copy: delivering a
record twice must not count it twice, and a failed export never blocks or changes the Run.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from omemo_content_factory.domain.analytics import AnalyticsRecord


class AnalyticsSinkError(Exception):
    """The sink did not accept the records (technical failure, not a ``DomainError``)."""


class AnalyticsSink(Protocol):
    """Where copies of Analytics Records are delivered (ADAPTER_SPEC §7)."""

    def export(self, records: Sequence[AnalyticsRecord], /) -> None:
        """Deliver the records; one already delivered (same ``record_id``) is not duplicated."""
        ...
