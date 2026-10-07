"""Making one picture from words, and from pictures the result must resemble (ADR-0088).

The first image port (``image_generator``) turns **one reference photo** into an ending frame. A
story is drawn differently: a character sheet comes from words alone, and every shot after it is
words plus the sheets of whoever is in the shot, so that the same character looks like the same
character. This is the port for that — zero or more references, a size asked for outright.

The vendor lives in ``infrastructure/`` and never reaches this contract; paths in, a path out, the
adapter's own measurements back (``GeneratedImage``, ADR-0056 §1). The call is synchronous, paid and
repeatable — repeating it spends again and overwrites the same destination, which corrupts nothing —
so it runs in a deterministic Workflow step, never inside a model's Tool budget (ADR-0054 §2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.adapters.image_generator import GeneratedImage, ImageGeneratorError

__all__ = ["FrameGenerator", "FrameRequest", "GeneratedImage", "ImageGeneratorError"]


@dataclass(frozen=True, slots=True)
class FrameRequest:
    """One picture: told what, shown whom to resemble, how big, written where."""

    prompt: str
    destination: str
    width: int
    height: int
    references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("prompt", "destination"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a frame request needs a non-blank {name}")
        for name in ("width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"a frame request needs a positive {name}")
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.references):
            raise ValueError("a frame request's references must be non-blank paths")
        if len(set(self.references)) != len(self.references):
            raise ValueError("a frame request names the same reference twice")


class FrameGenerator(Protocol):
    """Turns a prompt and optional reference pictures into one image file."""

    def generate(self, request: FrameRequest, /) -> GeneratedImage:
        """Write the image to ``request.destination`` and report what was written."""
        ...
