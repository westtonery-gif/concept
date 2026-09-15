"""Tests for ``current_date@v1`` (ADR-0022 §5). Maps TOOL_ACCEPTANCE.md §4 (CDT)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest

from omemo_content_factory.tools.contract import ToolCall, ToolExecutionError, ToolResultStatus
from omemo_content_factory.tools.current_date import CurrentDate
from omemo_content_factory.tools.toolbox import Toolbox

_MSK = timezone(timedelta(hours=3))


class _CountingClock:
    """A clock that returns the given moments in turn and counts how often it was read."""

    def __init__(self, *moments: datetime) -> None:
        self._moments = list(moments)
        self.reads = 0

    def __call__(self) -> datetime:
        moment = self._moments[min(self.reads, len(self._moments) - 1)]
        self.reads += 1
        return moment


def _clock_at(moment: datetime) -> Callable[[], datetime]:
    return lambda: moment


def test_cdt_01_reports_the_date_weekday_and_time_of_the_injected_clock() -> None:
    tool = CurrentDate(clock=_clock_at(datetime(2026, 9, 15, 9, 30, 12, 999, tzinfo=UTC)))
    assert tool.invoke({}) == {
        "date": "2026-09-15",
        "weekday": "Tuesday",
        "datetime": "2026-09-15T09:30:12+00:00",
    }


def test_cdt_02_the_date_is_the_clocks_local_date() -> None:
    # 01:30 in +03:00 is still the previous day in UTC; the clock's own zone decides.
    tool = CurrentDate(clock=_clock_at(datetime(2026, 9, 16, 1, 30, tzinfo=_MSK)))
    result = tool.invoke({})
    assert result["date"] == "2026-09-16"
    assert result["weekday"] == "Wednesday"
    assert result["datetime"] == "2026-09-16T01:30:00+03:00"


def test_cdt_03_the_clock_is_read_on_every_call() -> None:
    clock = _CountingClock(
        datetime(2026, 9, 15, 23, 59, tzinfo=UTC), datetime(2026, 9, 16, 0, 1, tzinfo=UTC)
    )
    tool = CurrentDate(clock=clock)
    assert tool.invoke({})["date"] == "2026-09-15"
    assert tool.invoke({})["date"] == "2026-09-16"
    assert clock.reads == 2


def test_cdt_04_a_naive_clock_is_a_tool_failure() -> None:
    tool = CurrentDate(clock=_clock_at(datetime(2026, 9, 15, 9, 30)))
    with pytest.raises(ToolExecutionError, match="naive"):
        tool.invoke({})

    result = Toolbox(grants=("current_date@v1",), available=[tool]).invoke(
        ToolCall(name="current_date")
    )
    assert result.status is ToolResultStatus.FAILED
    assert "naive" in result.error


def test_cdt_05_it_takes_no_arguments_and_an_extra_one_is_refused_unread() -> None:
    clock = _CountingClock(datetime(2026, 9, 15, 9, 30, tzinfo=UTC))
    toolbox = Toolbox(grants=("current_date@v1",), available=[CurrentDate(clock=clock)])

    result = toolbox.invoke(ToolCall(name="current_date", arguments={"timezone": "UTC"}))

    assert result.status is ToolResultStatus.REFUSED
    assert "unknown argument(s): timezone" in result.error
    assert clock.reads == 0
