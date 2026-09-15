"""``current_date@v1`` — tell the model today's date (ADR-0022 §5).

A model does not know what day it is, and briefs are full of "this week", seasonal topics and
"recent" sources. The clock is **injected** — the Tool never reads the system clock itself — which
is the pattern for every Tool that needs the world outside the process: the dependency arrives as a
port at construction (an Adapter after Stage 6), never as an import. The clock must be
timezone-aware; the reported date is the clock's local date, so the Composition Root decides the
editorial timezone by the clock it passes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import ClassVar

from omemo_content_factory.domain.tool import ToolDescriptor, ToolVersion
from omemo_content_factory.tools.contract import ToolArguments, ToolExecutionError, ToolValue

DESCRIPTOR = ToolDescriptor(
    tool_id="current_date",
    version=ToolVersion(1),
    description=(
        "Return today's date, weekday and current time with its UTC offset. Call it whenever the "
        "answer depends on the current date (this week, the season, how recent a source is): you "
        "do not know the date otherwise."
    ),
    parameters=(),
)

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
"""Fixed English names: ``strftime('%A')`` would depend on the process locale."""


class CurrentDate:
    """Reads the injected clock on every call. A ``Tool``."""

    __slots__ = ("_clock",)
    descriptor: ClassVar[ToolDescriptor] = DESCRIPTOR

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    def invoke(self, arguments: ToolArguments, /) -> dict[str, ToolValue]:
        now = self._clock()
        if now.utcoffset() is None:
            raise ToolExecutionError("the clock returned a naive datetime; a timezone is required")
        return {
            "date": now.date().isoformat(),
            "weekday": _WEEKDAYS[now.weekday()],
            "datetime": now.isoformat(timespec="seconds"),
        }
