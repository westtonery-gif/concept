"""Measuring a produced media file from its own bytes (ADR-0067 §4).

The generation adapters report measurements of the files they wrote (ADR-0066 §2/§3), and those
must come from the file, never be echoed from the request or from the vendor's say-so — the same
rule ``ffmpeg_clip_renderer`` keeps by reading back with ``ffprobe``. Here the formats are few and
their headers are simple, so the reading is done in stdlib: PNG, JPEG and WebP for the width and
height of an image; ISO base media (``.mp4``) for a video's duration and its video track's size.

Anything that cannot be read is ``MediaMeasureError``: a file we cannot measure is a file whose
format check could not be made, which is a failure, not a guess.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = [
    "ImageMeasure",
    "MediaMeasureError",
    "VideoMeasure",
    "measure_image",
    "measure_video",
    "sniff_image_type",
]


class MediaMeasureError(Exception):
    """The bytes are not a file of a format this module can measure."""


@dataclass(frozen=True, slots=True)
class ImageMeasure:
    media_type: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class VideoMeasure:
    container: str
    duration_ms: int
    width: int
    height: int


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def sniff_image_type(data: bytes) -> str | None:
    """The image's media type from its magic bytes, or ``None`` when it is none of the three."""
    if data.startswith(_PNG_SIGNATURE):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def measure_image(data: bytes) -> ImageMeasure:
    """Width and height of a PNG, JPEG or WebP image, read from its header."""
    media_type = sniff_image_type(data)
    try:
        if media_type == "image/png":
            width, height = _png_size(data)
        elif media_type == "image/jpeg":
            width, height = _jpeg_size(data)
        elif media_type == "image/webp":
            width, height = _webp_size(data)
        else:
            raise MediaMeasureError("the image is not a PNG, JPEG or WebP file")
    except (struct.error, IndexError) as exc:
        raise MediaMeasureError("the image header is truncated or damaged") from exc
    if width <= 0 or height <= 0:
        raise MediaMeasureError("the image header declares no picture")
    return ImageMeasure(media_type=media_type, width=width, height=height)


def _png_size(data: bytes) -> tuple[int, int]:
    if data[12:16] != b"IHDR":
        raise MediaMeasureError("a PNG must begin with its IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _jpeg_size(data: bytes) -> tuple[int, int]:
    at = 2
    while at + 4 <= len(data):
        if data[at] != 0xFF:
            raise MediaMeasureError("a JPEG marker is out of place")
        marker = data[at + 1]
        if marker == 0xFF:  # fill byte
            at += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length
            at += 2
            continue
        (length,) = struct.unpack(">H", data[at + 2 : at + 4])
        if marker in _JPEG_SOF:
            height, width = struct.unpack(">HH", data[at + 5 : at + 9])
            return int(width), int(height)
        if marker == 0xD9 or length < 2:
            break
        at += 2 + length
    raise MediaMeasureError("the JPEG has no frame header")


def _webp_size(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8L":
        if data[20] != 0x2F:
            raise MediaMeasureError("a lossless WebP has a bad signature")
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            raise MediaMeasureError("a lossy WebP has a bad start code")
        width, height = struct.unpack("<HH", data[26:30])
        return int(width) & 0x3FFF, int(height) & 0x3FFF
    raise MediaMeasureError("the WebP has no picture chunk")


def measure_video(data: bytes) -> VideoMeasure:
    """Duration and the video track's size of an MP4 (ISO base media) file."""
    try:
        top = dict(_boxes(data, 0, len(data)))
        if b"ftyp" not in top:
            raise MediaMeasureError("the video is not an MP4 file (no ftyp box)")
        if b"moov" not in top:
            raise MediaMeasureError("the MP4 has no movie header (no moov box)")
        moov_start, moov_end = top[b"moov"]
        duration_ms: int | None = None
        size: tuple[int, int] | None = None
        for kind, (start, end) in _boxes(data, moov_start, moov_end):
            if kind == b"mvhd":
                duration_ms = _mvhd_duration_ms(data, start)
            elif kind == b"trak" and size is None:
                size = _video_track_size(data, start, end)
    except (struct.error, IndexError) as exc:
        raise MediaMeasureError("the MP4 is truncated or damaged") from exc
    if duration_ms is None or duration_ms <= 0:
        raise MediaMeasureError("the MP4 declares no duration")
    if size is None or size[0] <= 0 or size[1] <= 0:
        raise MediaMeasureError("the MP4 has no video track with a picture size")
    return VideoMeasure(container="mp4", duration_ms=duration_ms, width=size[0], height=size[1])


def _boxes(data: bytes, start: int, end: int) -> list[tuple[bytes, tuple[int, int]]]:
    """The boxes between ``start`` and ``end`` as ``(type, (payload_start, box_end))``."""
    found: list[tuple[bytes, tuple[int, int]]] = []
    at = start
    while at + 8 <= end:
        (size,) = struct.unpack(">I", data[at : at + 4])
        kind = data[at + 4 : at + 8]
        header = 8
        if size == 1:
            (size,) = struct.unpack(">Q", data[at + 8 : at + 16])
            header = 16
        elif size == 0:
            size = end - at
        if size < header or at + size > end:
            raise MediaMeasureError("an MP4 box overruns its parent")
        found.append((kind, (at + header, at + size)))
        at += size
    return found


def _mvhd_duration_ms(data: bytes, at: int) -> int:
    version = data[at]
    if version == 1:
        timescale, duration = struct.unpack(">IQ", data[at + 20 : at + 32])
    else:
        timescale, duration = struct.unpack(">II", data[at + 12 : at + 20])
    if timescale <= 0:
        raise MediaMeasureError("the MP4 declares a zero timescale")
    return int(duration * 1000 // timescale)


def _video_track_size(data: bytes, start: int, end: int) -> tuple[int, int] | None:
    children = dict(_boxes(data, start, end))
    if b"tkhd" not in children or b"mdia" not in children:
        return None
    mdia_start, mdia_end = children[b"mdia"]
    handler = dict(_boxes(data, mdia_start, mdia_end)).get(b"hdlr")
    if handler is None or data[handler[0] + 8 : handler[0] + 12] != b"vide":
        return None
    at = children[b"tkhd"][0]
    offset = 84 if data[at] == 1 else 72  # past version/flags, times, ids, duration, matrix
    width, height = struct.unpack(">II", data[at + offset + 4 : at + offset + 12])
    return int(width) >> 16, int(height) >> 16
