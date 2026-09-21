"""Making the ending frame from the reference photo (ADR-0066 §2, GENERATION_SPEC §2).

The vendor lives in ``infrastructure/`` and never reaches this contract — the port is named by its
role, like every other outside system here (ADR-0023). Paths in, a path out: how the vendor wants
the photo encoded, and where it keeps the result, are its adapter's business (ADR-0066 §4).

The call is synchronous but still outside and paid, so it runs in a deterministic Workflow step,
never inside a model's Tool budget (ADR-0054 §2). Repeating it after a crash spends again and
overwrites the same destination; it corrupts nothing (ADR-0026 §4).

``GeneratedImage`` carries the adapter's **own measurements** of the file it wrote, as
``RenderedClip`` does (ADR-0056 §1): whatever checks the frame reads numbers, not a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    """One frame to make: from which photo, told what, written where."""

    reference: str
    prompt: str
    destination: str

    def __post_init__(self) -> None:
        for name in ("reference", "prompt", "destination"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"an image generation request needs a non-blank {name}")


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    """The file that was written, with the measurements read back from it."""

    path: str
    width: int
    height: int
    media_type: str

    def __post_init__(self) -> None:
        for name in ("width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"a generated image needs a positive {name}")
        for name in ("path", "media_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"a generated image needs a non-blank {name}")


class ImageGeneratorError(Exception):
    """The image could not be generated (technical failure, not a ``DomainError``)."""


class ImageGenerator(Protocol):
    """Turns a reference photo and a prompt into one image file."""

    def generate(self, request: ImageGenerationRequest, /) -> GeneratedImage:
        """Write the image to ``request.destination`` and report what was written."""
        ...
