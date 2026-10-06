"""Lays spoken lines one after another into a single dialogue track, with music ducked under them.

The speech port speaks one line (ADR-0085). A story video is a conversation, so something has to
put the lines in order, leave a breath between them, tuck a music bed under the talking and hand
back **when every word is said** — captions that light up the word being spoken read that timeline.

No ADR and no acceptance table for this one, on purpose: it is a small ffmpeg step, not a
decision. It follows the house rules anyway — ffmpeg is called through ``subprocess`` here in
``infrastructure/``, the file is written to a temporary name and renamed into place, and the
duration is read back from what was written rather than trusted from the arithmetic.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from omemo_content_factory.adapters.speech_synthesizer import SynthesizedSpeech

__all__ = [
    "DialogueLine",
    "DialogueMixer",
    "DialogueMixerError",
    "DialogueTrack",
    "DialogueWord",
    "lay_out",
]

_TIMEOUT_SECONDS = 600.0
_FADE_IN_MS = 1000
_FADE_OUT_MS = 1500


class DialogueMixerError(Exception):
    """The dialogue could not be put together (technical failure, not a ``DomainError``)."""


@dataclass(frozen=True, slots=True)
class DialogueLine:
    """One spoken line and who says it. ``pause_before_ms`` overrides the default breath."""

    speaker: str
    speech: SynthesizedSpeech
    pause_before_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.speaker.strip():
            raise ValueError("a dialogue line needs a speaker")
        if self.pause_before_ms is not None and self.pause_before_ms < 0:
            raise ValueError("a pause cannot be negative")


@dataclass(frozen=True, slots=True)
class DialogueWord:
    """A word on the whole track's clock, with who said it."""

    speaker: str
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class DialogueTrack:
    """The mixed file, measured, with the timeline of every word."""

    path: str
    duration_ms: int
    words: tuple[DialogueWord, ...]


def lay_out(
    lines: list[DialogueLine], *, gap_ms: int = 350, lead_ms: int = 250, tail_ms: int = 700
) -> tuple[list[int], int]:
    """Where each line starts on the track, and how long the track is. Pure arithmetic.

    The first line starts after ``lead_ms``; each next one after the previous ends plus its pause
    (``gap_ms`` unless the line says otherwise); the track ends ``tail_ms`` after the last line so
    the music has room to breathe.
    """
    if not lines:
        raise ValueError("a dialogue needs at least one line")
    starts: list[int] = []
    cursor = lead_ms
    for index, line in enumerate(lines):
        if index:
            cursor += gap_ms if line.pause_before_ms is None else line.pause_before_ms
        starts.append(cursor)
        cursor += line.speech.duration_ms
    return starts, cursor + tail_ms


class DialogueMixer:
    """Mixes a dialogue with ffmpeg: each line delayed into place, music ducked, level evened."""

    def __init__(self, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe

    def mix(
        self,
        lines: list[DialogueLine],
        destination: str,
        *,
        music: str | None = None,
        music_db: float = -18.0,
        loudness_lufs: float = -14.0,
        gap_ms: int = 350,
        lead_ms: int = 250,
        tail_ms: int = 700,
    ) -> DialogueTrack:
        """Write the track to ``destination`` (its extension picks the format) and measure it."""
        starts, total_ms = lay_out(lines, gap_ms=gap_ms, lead_ms=lead_ms, tail_ms=tail_ms)
        if music is not None and not Path(music).is_file():
            raise DialogueMixerError(f"the music file is missing: {music}")
        for line in lines:
            if not Path(line.speech.path).is_file():
                raise DialogueMixerError(f"a spoken line is missing: {line.speech.path}")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.stem}.tmp{target.suffix}")
        command = self._command(lines, starts, total_ms, temporary, music, music_db, loudness_lufs)
        try:
            self._run(command, what="the dialogue mix")
            duration_ms = self._duration_ms(temporary)
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        words = tuple(
            DialogueWord(line.speaker, word.text, start + word.start_ms, start + word.end_ms)
            for line, start in zip(lines, starts, strict=True)
            for word in line.speech.words
        )
        return DialogueTrack(path=str(target), duration_ms=duration_ms, words=words)

    def _command(
        self,
        lines: list[DialogueLine],
        starts: list[int],
        total_ms: int,
        output: Path,
        music: str | None,
        music_db: float,
        loudness_lufs: float,
    ) -> list[str]:
        command = [self._ffmpeg, "-y", "-v", "error"]
        for line in lines:
            command += ["-i", line.speech.path]
        if music is not None:
            command += ["-stream_loop", "-1", "-i", music]
        total_s = total_ms / 1000
        graph = [
            f"[{index}:a]aformat=sample_rates=48000:channel_layouts=mono,adelay={start}:all=1"
            f"[l{index}]"
            for index, start in enumerate(starts)
        ]
        labels = "".join(f"[l{index}]" for index in range(len(lines)))
        graph.append(
            f"{labels}amix=inputs={len(lines)}:normalize=0:duration=longest,"
            f"apad=whole_dur={total_s:.3f},atrim=0:{total_s:.3f}[dialogue]"
        )
        if music is None:
            graph.append(
                f"[dialogue]loudnorm=I={loudness_lufs}:TP=-1.5:LRA=11,aformat=channel_layouts=stereo[out]"
            )
        else:
            fade_out_start = max(total_s - _FADE_OUT_MS / 1000, 0)
            graph += [
                f"[{len(lines)}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                f"volume={music_db}dB,atrim=0:{total_s:.3f},"
                f"afade=t=in:d={_FADE_IN_MS / 1000},afade=t=out:st={fade_out_start:.3f}"
                f":d={_FADE_OUT_MS / 1000}[bed]",
                "[dialogue]asplit=2[key][voice]",
                "[bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=500[ducked]",
                "[voice]aformat=channel_layouts=stereo[voice2]",
                "[ducked][voice2]amix=inputs=2:normalize=0,"
                f"loudnorm=I={loudness_lufs}:TP=-1.5:LRA=11[out]",
            ]
        command += [
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[out]",
            "-ar",
            "48000",
            str(output),
        ]
        return command

    def _duration_ms(self, path: Path) -> int:
        report = self._run(
            [
                self._ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            what="measuring the dialogue",
        )
        try:
            duration_ms = round(float(report.strip()) * 1000)
        except ValueError:
            raise DialogueMixerError("the mixed dialogue has no readable duration") from None
        if duration_ms <= 0:
            raise DialogueMixerError("the mixed dialogue is empty")
        return duration_ms

    def _run(self, command: list[str], *, what: str) -> str:
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False
            )
        except FileNotFoundError:
            raise DialogueMixerError(f"{command[0]} is not installed or not on PATH") from None
        except subprocess.TimeoutExpired:
            raise DialogueMixerError(f"{what} took too long") from None
        if completed.returncode != 0:
            raise DialogueMixerError(f"{what} failed: {completed.stderr.strip()[-400:]}")
        return completed.stdout
