"""Bounded, reproducible picture treatments; no platform originality claims."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class VisualEffects:
    angle_degrees: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    sharpness: float = 0.0
    mirror: bool = False


def choose_effects(seed: str, look: str, mirror: bool = False) -> VisualEffects:
    if look not in ("clean", "subtle", "strong"):
        raise ValueError("look must be clean, subtle or strong")
    if type(mirror) is not bool:
        raise ValueError("mirror must be a boolean")
    if look == "clean":
        return VisualEffects(mirror=mirror)
    digest = hashlib.sha256(seed.encode()).digest()

    def sample(index: int, low: float, high: float) -> float:
        fraction = int.from_bytes(digest[index * 4 : index * 4 + 4], "big") / 0xFFFFFFFF
        return round(low + (high - low) * fraction, 6)

    strong = look == "strong"
    return VisualEffects(
        angle_degrees=sample(0, 0.8 if strong else 0.25, 1.6 if strong else 0.65)
        * (-1 if digest[-1] & 1 else 1),
        contrast=sample(1, 0.95 if strong else 0.98, 1.10 if strong else 1.03),
        saturation=sample(2, 0.92 if strong else 0.97, 1.12 if strong else 1.04),
        gamma=sample(3, 0.96 if strong else 0.985, 1.04 if strong else 1.015),
        sharpness=sample(4, 0.3 if strong else 0.1, 0.6 if strong else 0.25),
        mirror=mirror,
    )


def visual_filter(width: int, height: int, scale: float, effects: VisualEffects) -> str:
    angle = math.radians(effects.angle_degrees)
    if angle:
        sine, cosine = abs(math.sin(angle)), abs(math.cos(angle))
        # Two pixels of clearance at each edge, even for wide/portrait scale=1 inputs.
        scale = min(
            scale,
            (width - 4) / (width * cosine + height * sine),
            (height - 4) / (height * cosine + width * sine),
        )
        inner_w = max(2, math.floor(width * scale / 2) * 2)
        inner_h = max(2, math.floor(height * scale / 2) * 2)
    else:
        inner_w, inner_h = (
            max(2, round(width * scale / 2) * 2),
            max(2, round(height * scale / 2) * 2),
        )
    filters = [f"scale={inner_w}:{inner_h}:flags=lanczos", "setsar=1"]
    if effects.mirror:
        filters.append("hflip")
    if (effects.contrast, effects.saturation, effects.gamma) != (1, 1, 1):
        filters.append(
            f"eq=contrast={effects.contrast}:saturation={effects.saturation}:gamma={effects.gamma}"
        )
    if effects.sharpness:
        filters.append(f"unsharp=5:5:{effects.sharpness}:5:5:0")
    if angle:
        filters.append(f"rotate={angle:.12f}:ow={width}:oh={height}:c=black")
    else:
        filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    return ",".join(filters)
