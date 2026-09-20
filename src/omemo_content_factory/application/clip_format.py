"""Checking a rendered clip against the platform's limits — arithmetic, not judgement.

ADR-0056 §1: a model asked whether 118 seconds is within a 120-second limit will usually be right,
and usually is not a gate. So format compliance is computed here from the measurements the renderer
reported, and a violation is a ``FAILED`` Task with a stable reason rather than a ``flagged``
verdict queued for a person — a clip out of spec is the renderer doing other than it was told,
which is our defect, not a content risk.

Aspect ratio is deliberately **not** checked: v1 does not reframe, the clip keeps the source's
shape and the platform letterboxes it (ADR-0058).
"""

from __future__ import annotations

from dataclasses import dataclass

from omemo_content_factory.adapters.clip_renderer import RenderedClip

__all__ = ["ClipFormatLimits", "check_clip_format"]


@dataclass(frozen=True, slots=True)
class ClipFormatLimits:
    """What the target platform accepts. Configuration, never a column on the board."""

    max_duration_ms: int
    containers: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_duration_ms, bool)
            or not isinstance(self.max_duration_ms, int)
            or self.max_duration_ms <= 0
        ):
            raise ValueError("clip format limits need a positive max_duration_ms")
        if not isinstance(self.containers, tuple) or not self.containers:
            raise ValueError("clip format limits need at least one accepted container")
        if not all(isinstance(name, str) and name.strip() for name in self.containers):
            raise ValueError("every accepted container must be a non-blank string")


def check_clip_format(clip: RenderedClip, *, limits: ClipFormatLimits) -> tuple[str, ...]:
    """Every way ``clip`` breaks ``limits``; empty means it is fit to ship."""
    violations: list[str] = []
    if clip.duration_ms > limits.max_duration_ms:
        violations.append(
            f"duration {clip.duration_ms} ms exceeds the limit of {limits.max_duration_ms} ms"
        )
    accepted = {name.strip().casefold() for name in limits.containers}
    if clip.container.strip().casefold() not in accepted:
        violations.append(
            f"container {clip.container!r} is not one of {', '.join(sorted(accepted))}"
        )
    return tuple(violations)
