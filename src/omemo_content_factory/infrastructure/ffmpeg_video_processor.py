"""Local, recoverable 0.98x processing of finished cuts (ADR-0095)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omemo_content_factory.adapters.clip_renderer import ClipRendererError
from omemo_content_factory.adapters.video_processor import (
    ProcessedVideo,
    VideoProcessingError,
    VideoProcessingRequest,
)
from omemo_content_factory.infrastructure.ffmpeg_clip_renderer import FfmpegClipRenderer


class FfmpegVideoProcessor:
    def __init__(
        self,
        *,
        speed: float = 0.98,
        scale: float = 0.98,
        crf: int = 18,
        preset: str = "slow",
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        timeout: float = 600,
    ) -> None:
        if not 0.5 <= speed <= 1 or not 0.5 <= scale <= 1:
            raise ValueError("video speed and scale must be between 0.5 and 1")
        if isinstance(crf, bool) or not isinstance(crf, int) or not 0 <= crf <= 51:
            raise ValueError("video CRF must be an integer between 0 and 51")
        if preset not in (
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
            "veryslow",
        ):
            raise ValueError("invalid video encoder preset")
        self._speed, self._scale = speed, scale
        self._settings = {
            "version": 1,
            "speed": speed,
            "scale": scale,
            "crf": crf,
            "preset": preset,
        }
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._media = FfmpegClipRenderer(ffmpeg=ffmpeg, ffprobe=ffprobe, timeout=timeout)

    def process(self, request: VideoProcessingRequest, /) -> ProcessedVideo:
        try:
            return self._process(request)
        except (OSError, ValueError, KeyError, TypeError, ClipRendererError) as error:
            raise VideoProcessingError(str(error)) from error

    def _process(self, request: VideoProcessingRequest) -> ProcessedVideo:
        source = Path(request.source_path).resolve()
        destination = Path(request.destination).resolve()
        if not source.is_file():
            raise VideoProcessingError(f"the video to process is missing: {source}")
        if source == destination:
            raise VideoProcessingError("video processing must not overwrite its source")
        source_hash = _sha256(source)
        known = self._processed_input(source, source_hash)
        if known is not None:
            return known
        identity: dict[str, Any] = {
            "request_id": request.request_id,
            "source_sha256": source_hash,
            "source_path": str(source),
            "destination": str(destination),
            "settings": self._settings,
        }
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        marker = f"video-processing-v1:{fingerprint}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return self._recover(destination, identity, marker)
        report = self._probe(source)
        with tempfile.TemporaryDirectory(prefix=".processing-", dir=destination.parent) as folder:
            temporary = Path(folder) / "video.mp4"
            self._render(source, temporary, report, marker)
            if _sha256(source) != source_hash:
                raise VideoProcessingError("the source changed during video processing")
            self._media._measure(temporary)  # validate before publishing a final filename
            if _sha256(temporary) == source_hash:
                raise VideoProcessingError("processing did not change the video hash")
            # The final name is created atomically and never overwrites another job's file.
            with contextlib.suppress(FileExistsError):
                os.link(temporary, destination)
        return self._recover(destination, identity, marker)

    def _probe(self, path: Path) -> dict[str, Any]:
        raw = self._media._run(
            [
                self._ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            what="inspect video processing metadata",
        )
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise VideoProcessingError("ffprobe did not return a video description")
        return data

    def _processed_input(self, source: Path, digest: str) -> ProcessedVideo | None:
        report = source.with_suffix(".processing.json")
        if not report.is_file():
            metadata = self._probe(source).get("format", {}).get("tags", {})
            if str(metadata.get("comment", "")).startswith("video-processing-v1:"):
                raise VideoProcessingError("already processed input is missing its report")
            return None
        data = _read_report(report)
        if data.get("settings") != self._settings:
            raise VideoProcessingError("input was already processed with different settings")
        if data.get("output_sha256") != digest or data.get("destination") != str(source):
            raise VideoProcessingError("processed input does not match its report")
        return ProcessedVideo(
            self._media._measure(source), data["source_sha256"], digest, str(report)
        )

    def _recover(self, path: Path, identity: dict[str, Any], marker: str) -> ProcessedVideo:
        report_path = path.with_suffix(".processing.json")
        metadata = self._probe(path).get("format", {}).get("tags", {})
        if metadata.get("comment") != marker:
            raise VideoProcessingError("destination belongs to another processing request")
        digest = _sha256(path)
        if report_path.exists():
            stored = _read_report(report_path)
            if any(stored.get(key) != value for key, value in identity.items()):
                raise VideoProcessingError("processing report belongs to a different request")
            if stored.get("output_sha256") != digest:
                raise VideoProcessingError("processed output failed its hash check")
        result = ProcessedVideo(
            self._media._measure(path), identity["source_sha256"], digest, str(report_path)
        )
        if not report_path.exists():
            payload = {**identity, "output_sha256": digest, "clip": asdict(result.clip)}
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as file:
                temporary = Path(file.name)
                json.dump(payload, file, ensure_ascii=False, sort_keys=True)
            try:
                os.link(temporary, report_path)
            except FileExistsError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
        return result

    def _render(self, source: Path, target: Path, report: dict[str, Any], marker: str) -> None:
        video = next(
            (
                s
                for s in report.get("streams", [])
                if s.get("codec_type") == "video"
                and not s.get("disposition", {}).get("attached_pic")
            ),
            None,
        )
        if video is None:
            raise VideoProcessingError("input has no video stream")
        if video.get("color_transfer") in ("smpte2084", "arib-std-b67"):
            raise VideoProcessingError(
                "HDR video requires explicit SDR conversion before processing"
            )
        width, height = _dimensions(video)
        speed, scale = self._speed, self._scale
        inner_w, inner_h = (
            max(2, round(width * scale / 2) * 2),
            max(2, round(height * scale / 2) * 2),
        )
        filters = (
            f"scale={inner_w}:{inner_h}:flags=lanczos,setsar=1,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setpts=(PTS-STARTPTS)/{speed},format=yuv420p"
        )
        command = [
            self._ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-n",
            "-i",
            str(source),
            "-map",
            f"0:{video['index']}",
            "-map",
            "0:a:0?",
            "-vf",
            filters,
            "-map_metadata",
            "-1",
            "-map_metadata:s",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "libx264",
            "-crf",
            str(self._settings["crf"]),
            "-preset",
            str(self._settings["preset"]),
            "-fps_mode",
            "passthrough",
            "-video_track_timescale",
            "90000",
        ]
        audio = next((s for s in report.get("streams", []) if s.get("codec_type") == "audio"), None)
        if audio is not None:
            offset = (float(audio.get("start_time", 0)) - float(video.get("start_time", 0))) / speed
            command += [
                "-af",
                f"asetpts=PTS-STARTPTS+({offset})/TB,atempo={speed}",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        command += [
            "-metadata",
            f"title=Processed clip {marker[-16:]}",
            "-metadata",
            f"comment={marker}",
            "-metadata",
            f"creation_time={datetime.now(UTC).isoformat()}",
            "-movflags",
            "+faststart",
            str(target),
        ]
        self._media._run(command, what="process the clipped video")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dimensions(video: dict[str, Any]) -> tuple[int, int]:
    width, height = int(video["width"]), int(video["height"])
    if min(width, height) < 16:
        raise VideoProcessingError("video dimensions are too small")
    sar = str(video.get("sample_aspect_ratio", "1:1")).split(":")
    if len(sar) == 2 and all(part.isdigit() and int(part) > 0 for part in sar):
        width = round(width * int(sar[0]) / int(sar[1]))
    rotation = next(
        (s.get("rotation", 0) for s in video.get("side_data_list", []) if "rotation" in s), 0
    )
    if abs(round(float(rotation))) % 180 == 90:
        width, height = height, width
    return max(2, width // 2 * 2), max(2, height // 2 * 2)


def _read_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise VideoProcessingError("processing report must be a JSON object")
    return data
