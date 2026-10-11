"""Picking an episode's strongest moments: the model's input, and the only judge of its answer.

ADR-0097. A model reads the numbered transcript and names moments by the **lines** they span, so a
boundary is a line edge by construction. Everything here is pure: the input is built from the
indexed footage, and ``decode_moments`` alone decides whether an answer is acceptable — it reports
every fault together and never repairs an answer by guessing (the shape of ``decode_verdict``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import pairwise

from omemo_content_factory.adapters.episode_board import ClipMode
from omemo_content_factory.adapters.footage_index import IndexedFootage, SpeechSpan
from omemo_content_factory.application.clip_plan import ClipPlan, planned_clip

__all__ = [
    "KINDS",
    "MOMENTS_FIELD",
    "Moment",
    "MomentLimits",
    "MomentsError",
    "decode_moments",
    "moments_task_input",
    "plan_from_moments",
]

MOMENTS_FIELD = "moments"
"""The one field of the ``clip-moments@v1`` Output."""

KINDS = ("laugh", "surprise", "tension", "emotion", "quote")
"""The closed vocabulary of why a moment was picked (ADR-0097 §2)."""

_RANGE = re.compile(r"^(\d+)(?:\s*-\s*(\d+))?$")


@dataclass(frozen=True, slots=True)
class MomentLimits:
    """How long a moment may be and how many an episode may yield (ADR-0097 §7)."""

    min_ms: int
    max_ms: int
    count: int

    def __post_init__(self) -> None:
        for name, value in (
            ("min_ms", self.min_ms),
            ("max_ms", self.max_ms),
            ("count", self.count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"moment limits need a positive whole {name}")
        if self.min_ms > self.max_ms:
            raise ValueError("moment limits need min_ms not above max_ms")


@dataclass(frozen=True, slots=True)
class Moment:
    """One picked moment: the lines it spans, its interval and why it was chosen."""

    first_line: int
    last_line: int
    start_ms: int
    end_ms: int
    kind: str
    why: str


class MomentsError(Exception):
    """An answer broke the contract; ``faults`` lists every break, for one repair round."""

    def __init__(self, faults: tuple[str, ...]) -> None:
        super().__init__("; ".join(faults))
        self.faults = faults


def _listed(footage: IndexedFootage) -> dict[int, SpeechSpan]:
    """The lines the model is shown, by number: every line that is not inside a skip zone."""
    return {
        number: span
        for number, span in enumerate(footage.speech, start=1)
        if span.text.strip()
        and not any(
            span.start_ms < zone.end_ms and span.end_ms > zone.start_ms for zone in footage.skips
        )
    }


def moments_task_input(
    footage: IndexedFootage,
    limits: MomentLimits,
    *,
    episode_file: str,
    faults: tuple[str, ...] = (),
) -> str:
    """The canonical JSON the ``pick-moments`` Task is opened with; ``faults`` on a repair round."""
    payload: dict[str, object] = {
        "episode_file": episode_file,
        "duration_s": round(footage.duration_ms / 1000),
        "min_clip_s": limits.min_ms // 1000,
        "max_clip_s": limits.max_ms // 1000,
        "moments_wanted": limits.count,
        "transcript": [
            f"{number} | {span.start_ms / 1000:.1f} | {span.end_ms / 1000:.1f} | "
            f"{span.text.strip()}"
            for number, span in _listed(footage).items()
        ],
    }
    if faults:
        payload["previous_answer_faults"] = list(faults)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def decode_moments(
    text: str, footage: IndexedFootage, limits: MomentLimits, /
) -> tuple[Moment, ...]:
    """The moments in ``text`` (one ``first-last | kind | why`` per line), or every fault found."""
    lines = _listed(footage)
    faults: list[str] = []
    moments: list[Moment] = []
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    if not rows:
        raise MomentsError(("the answer names no moments",))
    for position, row in enumerate(rows, start=1):
        moment = _row(position, row, lines, footage, limits, faults)
        if moment is not None:
            moments.append(moment)
    if len(rows) > limits.count:
        faults.append(f"{len(rows)} moments, at most {limits.count} are wanted")
    ordered = sorted(moments, key=lambda moment: moment.start_ms)
    for earlier, later in pairwise(ordered):
        if later.start_ms < earlier.end_ms:
            faults.append(
                f"lines {earlier.first_line}-{earlier.last_line} and "
                f"{later.first_line}-{later.last_line} overlap"
            )
    if faults:
        raise MomentsError(tuple(faults))
    return tuple(moments)


def _row(
    position: int,
    row: str,
    lines: dict[int, SpeechSpan],
    footage: IndexedFootage,
    limits: MomentLimits,
    faults: list[str],
) -> Moment | None:
    label = f"moment {position}"
    parts = [part.strip() for part in row.split("|", 2)]
    if len(parts) != 3:
        faults.append(f"{label}: expected 'first-last | kind | why'")
        return None
    found = _RANGE.match(parts[0])
    problems = _kind_problems(parts[1], parts[2])
    if found is None:
        problems.append("lines must be written 'first-last' or one number")
        faults.extend(f"{label}: {problem}" for problem in problems)
        return None
    first = int(found.group(1))
    last = int(found.group(2) or found.group(1))
    problems.extend(_line_problems(first, last, lines, footage, limits))
    faults.extend(f"{label}: {problem}" for problem in problems)
    if problems:
        return None
    return Moment(
        first, last, lines[first].start_ms, lines[last].end_ms, parts[1].casefold(), parts[2]
    )


def _kind_problems(kind: str, why: str) -> list[str]:
    problems: list[str] = []
    if kind.casefold() not in KINDS:
        problems.append(f"kind {kind!r} is not one of {', '.join(KINDS)}")
    if not why:
        problems.append("the reason is blank")
    return problems


def _line_problems(
    first: int,
    last: int,
    lines: dict[int, SpeechSpan],
    footage: IndexedFootage,
    limits: MomentLimits,
) -> list[str]:
    problems: list[str] = []
    if first > last:
        problems.append(f"first line {first} is after last line {last}")
    problems.extend(f"line {n} is not a listed line" for n in (first, last) if n not in lines)
    if problems:
        return problems
    start, end = lines[first].start_ms, lines[last].end_ms
    if not limits.min_ms <= end - start <= limits.max_ms:
        problems.append(
            f"it lasts {(end - start) / 1000:.0f} s, allowed "
            f"{limits.min_ms // 1000}-{limits.max_ms // 1000} s"
        )
    if any(start < zone.end_ms and end > zone.start_ms for zone in footage.skips):
        problems.append("it runs through the opening titles or the end credits")
    return problems


def plan_from_moments(
    footage: IndexedFootage, moments: tuple[Moment, ...], *, episode_ref: str
) -> ClipPlan:
    """The clips of the picked moments, **in episode order** (ADR-0097 §4); pure."""
    if not episode_ref.strip():
        raise ValueError("plan_from_moments needs a non-blank episode_ref")
    ordered = sorted(moments, key=lambda moment: moment.start_ms)
    clips = tuple(
        planned_clip(footage, number, moment.start_ms, moment.end_ms)
        for number, moment in enumerate(ordered, start=1)
    )
    return ClipPlan(episode_ref=episode_ref, mode=ClipMode.MOMENTS, clips=clips)
