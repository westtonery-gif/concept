"""The episode's index as a recorded Output, and the transcript QA reads from it (ADR-0068).

Pure functions only: canonical JSON in and out of ``IndexedFootage``, and the whole-episode
transcript rendered from it. Recording the index in the Run is what lets a resumed invocation judge
and re-plan from exactly what the clips were cut from, without running whisper again (ADR-0068 §2).
"""

from __future__ import annotations

import json

from omemo_content_factory.adapters.footage_index import (
    IndexedFootage,
    SceneBreak,
    SkipZone,
    SpeechSpan,
)

__all__ = [
    "FOOTAGE_SCHEMA_REF",
    "FootageRecordError",
    "episode_transcript",
    "footage_from_payload",
    "footage_to_payload",
]

FOOTAGE_SCHEMA_REF = "footage-index@v1"
"""The opaque reference of the index Output. Produced by tools, not a model, like a clip's."""


class FootageRecordError(Exception):
    """A recorded index could not be read back (application error, not domain)."""


def footage_to_payload(footage: IndexedFootage, /) -> str:
    """The canonical JSON the index Task records as its Output."""
    return json.dumps(
        {
            "duration_ms": footage.duration_ms,
            "scenes": [scene.at_ms for scene in footage.scenes],
            "speech": [
                {"start_ms": span.start_ms, "end_ms": span.end_ms, "text": span.text}
                for span in footage.speech
            ],
            "skips": [{"start_ms": zone.start_ms, "end_ms": zone.end_ms} for zone in footage.skips],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def footage_from_payload(payload: str, /) -> IndexedFootage:
    """Read a recorded index back; the port's own invariants re-check it."""
    try:
        raw = json.loads(payload)
        return IndexedFootage(
            duration_ms=raw["duration_ms"],
            scenes=tuple(SceneBreak(at_ms=at_ms) for at_ms in raw["scenes"]),
            speech=tuple(
                SpeechSpan(start_ms=span["start_ms"], end_ms=span["end_ms"], text=span["text"])
                for span in raw["speech"]
            ),
            # An index recorded before ADR-0074 has no skips: it reads back with none (§3).
            skips=tuple(
                SkipZone(start_ms=zone["start_ms"], end_ms=zone["end_ms"])
                for zone in raw.get("skips", ())
            ),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise FootageRecordError(f"the recorded footage index is unreadable: {error}") from error


def episode_transcript(footage: IndexedFootage, /) -> str:
    """The whole episode, one line per span: ``[start_ms–end_ms] text`` (ADR-0068 §3).

    Milliseconds, because a clip's own bounds are: finding the clip in the episode is then a
    comparison, not arithmetic for the model. The same string for every clip of an episode.
    """
    return "\n".join(
        f"[{span.start_ms}–{span.end_ms}] {span.text.strip()}"
        for span in footage.speech
        if span.text.strip()
    )
