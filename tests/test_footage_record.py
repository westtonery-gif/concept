"""Tests for the recorded footage index and the transcript QA reads (ADR-0068 §2-3, CRN-12)."""

from __future__ import annotations

import pytest

from omemo_content_factory.adapters.footage_index import IndexedFootage, SceneBreak, SpeechSpan
from omemo_content_factory.application.footage_record import (
    FootageRecordError,
    episode_transcript,
    footage_from_payload,
    footage_to_payload,
)

FOOTAGE = IndexedFootage(
    duration_ms=60_000,
    scenes=(SceneBreak(20_000), SceneBreak(40_000)),
    speech=(
        SpeechSpan(start_ms=1_000, end_ms=2_500, text="Морти, пошли."),
        SpeechSpan(start_ms=41_000, end_ms=43_000, text="Куда, Рик?"),
    ),
)


def test_crn_12_the_index_survives_the_round_trip_exactly() -> None:
    assert footage_from_payload(footage_to_payload(FOOTAGE)) == FOOTAGE


def test_crn_12_the_payload_is_canonical() -> None:
    assert footage_to_payload(FOOTAGE) == footage_to_payload(FOOTAGE)
    assert "Морти" in footage_to_payload(FOOTAGE), "stored readable, not \\u-escaped"


def test_crn_12_the_transcript_is_one_timed_line_per_span_in_milliseconds() -> None:
    assert episode_transcript(FOOTAGE) == ("[1000–2500] Морти, пошли.\n[41000–43000] Куда, Рик?")


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "{}",
        '{"duration_ms": 0, "scenes": [], "speech": []}',
        '{"duration_ms": 10, "scenes": [], "speech": [{"start_ms": 5, "end_ms": 1, "text": "x"}]}',
    ],
)
def test_crn_12_an_unreadable_record_is_refused_not_guessed(payload: str) -> None:
    with pytest.raises(FootageRecordError):
        footage_from_payload(payload)
