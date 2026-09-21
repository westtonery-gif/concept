"""Real, minimal media files built in stdlib for the generation tests (GENERATION_ACCEPTANCE).

Each builder writes the bytes a real encoder would put in the header the measurement reads — a PNG
with a valid IHDR and pixel data, a JPEG with a frame header, a WebP with a VP8X chunk, an MP4 with
``ftyp`` + ``moov`` (``mvhd``, and a ``trak`` whose ``hdlr`` names it a video track). Nothing here
is a mock of the reader: the reader parses these the way it parses a vendor's file.
"""

from __future__ import annotations

import struct
import zlib


def png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x11\x00" * 3
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def webp(width: int, height: int) -> bytes:
    payload = (
        b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    )
    chunk = b"VP8X" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4(duration_ms: int, width: int, height: int, *, version: int = 0) -> bytes:
    timescale = 1000
    if version == 1:
        mvhd = struct.pack(">B3xQQIQ", 1, 0, 0, timescale, duration_ms) + b"\x00" * 80
        tkhd_head = struct.pack(">B3xQQII", 1, 0, 0, 1, 0) + struct.pack(">Q", duration_ms)
    else:
        mvhd = struct.pack(">B3xIIII", 0, 0, 0, timescale, duration_ms) + b"\x00" * 80
        tkhd_head = struct.pack(">B3xIIIII", 0, 0, 0, 1, 0, duration_ms)

    def tkhd(w: int, h: int) -> bytes:
        return tkhd_head + b"\x00" * 16 + b"\x00" * 36 + struct.pack(">II", w << 16, h << 16)

    hdlr = struct.pack(">B3xI", 0, 0) + b"vide" + b"\x00" * 12 + b"VideoHandler\x00"
    trak = _box(b"trak", _box(b"tkhd", tkhd(width, height)) + _box(b"mdia", _box(b"hdlr", hdlr)))
    sound_hdlr = struct.pack(">B3xI", 0, 0) + b"soun" + b"\x00" * 12 + b"\x00"
    sound = _box(b"trak", _box(b"tkhd", tkhd(0, 0)) + _box(b"mdia", _box(b"hdlr", sound_hdlr)))
    moov = _box(b"moov", _box(b"mvhd", mvhd) + sound + trak)
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41") + _box(b"mdat", b"\x00" * 64) + moov
