"""Tests for the dialogue mixer: the layout arithmetic, and a real ffmpeg mix of generated tones."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from omemo_content_factory.adapters.speech_synthesizer import SpokenWord, SynthesizedSpeech
from omemo_content_factory.infrastructure.dialogue_mixer import (
    DialogueLine,
    DialogueMixer,
    DialogueMixerError,
    lay_out,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are not installed on this machine",
)


def speech(path: str, duration_ms: int, text: str = "Hello there") -> SynthesizedSpeech:
    first, _, second = text.partition(" ")
    half = duration_ms // 2
    return SynthesizedSpeech(
        path=path,
        duration_ms=duration_ms,
        sample_rate=24000,
        words=(SpokenWord(first, 0, half), SpokenWord(second, half, duration_ms)),
    )


def test_lay_out_puts_lines_after_each_other_with_breaths() -> None:
    lines = [
        DialogueLine("a", speech("/1.wav", 1000)),
        DialogueLine("b", speech("/2.wav", 2000)),
        DialogueLine("a", speech("/3.wav", 500), pause_before_ms=1000),
    ]
    starts, total = lay_out(lines, gap_ms=300, lead_ms=200, tail_ms=700)
    assert starts == [200, 1500, 4500]
    assert total == 4500 + 500 + 700


def test_lay_out_needs_a_line_and_a_line_needs_a_speaker() -> None:
    with pytest.raises(ValueError):
        lay_out([])
    with pytest.raises(ValueError):
        DialogueLine(" ", speech("/1.wav", 1000))
    with pytest.raises(ValueError):
        DialogueLine("a", speech("/1.wav", 1000), pause_before_ms=-1)


def tone(path: Path, seconds: float, hertz: int = 300) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={hertz}:sample_rate=24000:duration={seconds}",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )


def mean_volume(path: Path, start: float, length: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-ss",
            str(start),
            "-t",
            str(length),
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"mean_volume: (-?[0-9.]+) dB", result.stderr)
    assert match is not None
    return float(match.group(1))


@needs_ffmpeg
def test_the_mix_is_as_long_as_the_layout_says_and_the_words_sit_on_the_clock(
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "1.wav", tmp_path / "2.wav"
    tone(first, 1.0)
    tone(second, 1.5, 500)
    lines = [
        DialogueLine("ann", speech(str(first), 1000, "Look here")),
        DialogueLine("bob", speech(str(second), 1500, "Why though")),
    ]
    track = DialogueMixer().mix(lines, str(tmp_path / "out" / "dialogue.wav"))
    expected = 250 + 1000 + 350 + 1500 + 700
    assert abs(track.duration_ms - expected) <= 60
    assert [(w.speaker, w.text, w.start_ms) for w in track.words] == [
        ("ann", "Look", 250),
        ("ann", "here", 750),
        ("bob", "Why", 1600),
        ("bob", "though", 2350),
    ]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["dialogue.wav"]
    # the silent breath between the lines is quiet, the lines are not
    assert mean_volume(Path(track.path), 0.4, 0.5) > mean_volume(Path(track.path), 1.3, 0.2) + 20


@needs_ffmpeg
def test_music_is_ducked_under_the_voice_and_comes_back_after_it(tmp_path: Path) -> None:
    line, bed = tmp_path / "1.wav", tmp_path / "bed.wav"
    tone(line, 2.0, 300)
    tone(bed, 6.0, 900)
    lines = [DialogueLine("ann", speech(str(line), 2000, "Look here"))]
    plain = DialogueMixer().mix(lines, str(tmp_path / "plain.wav"), tail_ms=3000)
    mixed = DialogueMixer().mix(
        lines, str(tmp_path / "mixed.wav"), music=str(bed), music_db=-6.0, tail_ms=3000
    )
    assert abs(plain.duration_ms - mixed.duration_ms) <= 60
    # after the line the plain track is silent; the mixed one carries the music
    assert mean_volume(Path(plain.path), 3.5, 1.0) < -60
    assert mean_volume(Path(mixed.path), 3.5, 1.0) > -40


@needs_ffmpeg
def test_a_missing_file_is_named_and_nothing_is_written(tmp_path: Path) -> None:
    lines = [DialogueLine("ann", speech(str(tmp_path / "nope.wav"), 1000))]
    with pytest.raises(DialogueMixerError, match=r"nope\.wav"):
        DialogueMixer().mix(lines, str(tmp_path / "out.wav"))
    assert list(tmp_path.iterdir()) == []
    present = tmp_path / "1.wav"
    tone(present, 1.0)
    with pytest.raises(DialogueMixerError, match="music"):
        DialogueMixer().mix(
            [DialogueLine("ann", speech(str(present), 1000))],
            str(tmp_path / "out.wav"),
            music=str(tmp_path / "no-music.mp3"),
        )
    assert not (tmp_path / "out.wav").exists()


def test_a_missing_ffmpeg_is_a_clear_error(tmp_path: Path) -> None:
    present = tmp_path / "1.wav"
    present.write_bytes(b"x")
    with pytest.raises(DialogueMixerError, match="not installed"):
        DialogueMixer(ffmpeg="no-such-ffmpeg-binary").mix(
            [DialogueLine("ann", speech(str(present), 1000))], str(tmp_path / "out.wav")
        )
