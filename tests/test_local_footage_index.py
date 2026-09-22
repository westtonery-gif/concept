"""Tests for the vendorless ``FootageIndex`` (ADR-0064).

Duration and scene detection run against **real** ffmpeg on a video the test generates with a hard
cut in a known place, so a wrong filter string or a wrong parse fails here. Transcription is driven
through a stub `whisper-cli` — a real one needs a multi-gigabyte model that is operator setup, not
a test fixture — plus one live test that runs the real binary when a model happens to be
configured, and skips honestly when it is not.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from omemo_content_factory.adapters.episode_source import LocatedEpisode
from omemo_content_factory.adapters.footage_index import FootageIndex, FootageIndexError
from omemo_content_factory.infrastructure.local_footage_index import (
    LANGUAGE_VAR,
    MODEL_VAR,
    SCENE_THRESHOLD_VAR,
    LocalFootageIndex,
    WhisperSettings,
    whisper_settings_from_env,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are not installed on this machine",
)

TRANSCRIPT = {
    "transcription": [
        {"offsets": {"from": 0, "to": 1_500}, "text": " Первая реплика."},
        {"offsets": {"from": 1_500, "to": 3_000}, "text": " Вторая реплика."},
    ]
}


@pytest.fixture(scope="module")
def episode(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Two seconds of black followed by two of white — one unmistakable cut near 2.0 s."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    workspace = tmp_path_factory.mktemp("footage")
    parts = []
    for name, colour in (("black.mp4", "black"), ("white.mp4", "white")):
        part = workspace / name
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={colour}:duration=2:size=160x120:rate=10",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                str(part),
            ],
            capture_output=True,
            check=True,
            timeout=120,
        )
        parts.append(part)
    listing = workspace / "parts.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    episode = workspace / "episode.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(episode),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    yield episode


@pytest.fixture
def stub_whisper(tmp_path: Path) -> Path:
    """A `whisper-cli` that writes the canned transcript where the real one would."""
    script = tmp_path / "whisper-cli"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "argv = sys.argv\n"
        "out = argv[argv.index('-of') + 1]\n"
        f"open(out + '.json', 'w').write(json.dumps({json.dumps(TRANSCRIPT)}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _settings(tmp_path: Path, **overrides: object) -> WhisperSettings:
    model = tmp_path / "model.bin"
    model.write_bytes(b"not a real model, but a real file")
    fields: dict[str, object] = {"model": model, "language": "ru", "scene_threshold": 10.0}
    fields.update(overrides)
    return WhisperSettings(**fields)  # type: ignore[arg-type]


def _index(tmp_path: Path, stub: Path) -> FootageIndex:
    return LocalFootageIndex(_settings(tmp_path), whisper=str(stub), timeout=120.0)


# --- 1. The three answers the port owes -------------------------------------------------


@needs_ffmpeg
def test_fix_01_an_episode_is_measured_cut_and_transcribed(
    episode: Path, tmp_path: Path, stub_whisper: Path
) -> None:
    footage = _index(tmp_path, stub_whisper).index(
        LocatedEpisode(source_ref="episode.mp4", path=str(episode))
    )
    assert abs(footage.duration_ms - 4_000) <= 250
    # Concatenating two encoded parts shifts the join by a frame or two, so the cut lands near
    # 2.0 s rather than exactly on it. One cut, where the cut is, is the claim worth making.
    assert len(footage.scenes) == 1
    assert abs(footage.scenes[0].at_ms - 2_000) <= 100
    assert [span.text for span in footage.speech] == ["Первая реплика.", "Вторая реплика."]
    assert footage.speech[0].start_ms == 0


@needs_ffmpeg
def test_fix_02_a_higher_threshold_finds_fewer_scenes(
    episode: Path, tmp_path: Path, stub_whisper: Path
) -> None:
    """The threshold is configuration precisely because it is a judgement about footage."""
    located = LocatedEpisode(source_ref="episode.mp4", path=str(episode))
    strict = LocalFootageIndex(
        _settings(tmp_path, scene_threshold=99.0), whisper=str(stub_whisper), timeout=120.0
    )
    assert strict.index(located).scenes == ()


# --- 2. Whisper's output is cleaned, never propagated raw --------------------------------


@needs_ffmpeg
@pytest.mark.parametrize(
    ("segments", "expected"),
    [
        ([{"offsets": {"from": 0, "to": 500}, "text": "   "}], []),
        ([{"offsets": {"from": 0, "to": 0}, "text": "zero length"}], []),
        ([{"offsets": {"from": 0, "to": 500}}], []),
        ([{"text": "no offsets"}], []),
        (["not a segment"], []),
        (
            [
                {"offsets": {"from": 0, "to": 2_000}, "text": "first"},
                {"offsets": {"from": 1_000, "to": 3_000}, "text": "overlaps"},
            ],
            [(0, 2_000, "first"), (2_000, 3_000, "overlaps")],
        ),
        (
            [{"offsets": {"from": 0, "to": 99_000}, "text": "runs past the end"}],
            [(0, None, "runs past the end")],  # None = clamped to the episode's real duration
        ),
    ],
)
def test_fix_03_ill_formed_segments_are_dropped_or_clamped(
    episode: Path, tmp_path: Path, segments: list[object], expected: list[tuple[int, int, str]]
) -> None:
    """Whisper is a machine listening to speech, not a contract; the port's rules are stricter."""
    script = tmp_path / "whisper-cli"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "argv = sys.argv\n"
        "out = argv[argv.index('-of') + 1]\n"
        f"open(out + '.json', 'w').write(json.dumps({json.dumps({'transcription': segments})}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    footage = _index(tmp_path, script).index(
        LocatedEpisode(source_ref="episode.mp4", path=str(episode))
    )
    actual = [(s.start_ms, s.end_ms, s.text) for s in footage.speech]
    resolved = [(a, footage.duration_ms if b is None else b, c) for a, b, c in expected]
    assert actual == resolved


@needs_ffmpeg
def test_fix_09_whisper_is_told_not_to_loop(episode: Path, tmp_path: Path) -> None:
    """No text context carried across windows and no non-speech tokens (task 24.2).

    The real episode's title music became one phrase repeated for over two minutes, and the loop
    swallowed the dialogue after it; ``-mc 0`` is what brought the dialogue back.
    """
    argv_file = tmp_path / "argv.json"
    script = tmp_path / "whisper-cli"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "argv = sys.argv\n"
        f"open({str(argv_file)!r}, 'w').write(json.dumps(argv))\n"
        "out = argv[argv.index('-of') + 1]\n"
        f"open(out + '.json', 'w').write(json.dumps({json.dumps(TRANSCRIPT)}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    _index(tmp_path, script).index(LocatedEpisode(source_ref="episode.mp4", path=str(episode)))
    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv[argv.index("-mc") + 1] == "0"
    assert "-sns" in argv


def _loop(text: str, count: int, *, start: int = 0) -> list[dict[str, object]]:
    return [
        {"offsets": {"from": start + 300 * n, "to": start + 300 * (n + 1)}, "text": text}
        for n in range(count)
    ]


@needs_ffmpeg
@pytest.mark.parametrize(
    ("segments", "expected"),
    [
        # The first real episode, in miniature: a loop, then the dialogue it sat on.
        (
            [*_loop(" Девушки отдыхают", 5), *_loop(" Рик и Морти", 1, start=1_500)],
            ["Рик и Морти"],
        ),
        # Case, punctuation and spacing do not disguise a loop.
        (
            [
                {"offsets": {"from": 0, "to": 500}, "text": " Девушки отдыхают."},
                {"offsets": {"from": 500, "to": 900}, "text": "девушки  отдыхают"},
                {"offsets": {"from": 900, "to": 1_300}, "text": " Девушки отдыхают!"},
            ],
            [],
        ),
        # Two in a row is ordinary speech, and so is a phrase that comes back later.
        (_loop(" Нет.", 2), ["Нет.", "Нет."]),
        (
            [
                *_loop(" Морти!", 2),
                {"offsets": {"from": 600, "to": 900}, "text": " Что?"},
                *_loop(" Морти!", 1, start=900),
            ],
            ["Морти!", "Морти!", "Что?", "Морти!"],
        ),
    ],
)
def test_fix_10_a_repeated_phrase_is_a_loop_and_is_dropped(
    episode: Path, tmp_path: Path, segments: list[object], expected: list[str]
) -> None:
    """Three or more consecutive segments saying one thing go, the whole run (task 24.2)."""
    script = tmp_path / "whisper-cli"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "argv = sys.argv\n"
        "out = argv[argv.index('-of') + 1]\n"
        f"open(out + '.json', 'w').write(json.dumps({json.dumps({'transcription': segments})}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    footage = _index(tmp_path, script).index(
        LocatedEpisode(source_ref="episode.mp4", path=str(episode))
    )
    assert [span.text for span in footage.speech] == expected


def _stub_fpcalc(tmp_path: Path, prints: dict[str, list[int]]) -> Path:
    """An ``fpcalc`` that answers each file name with a scripted fingerprint."""
    script = tmp_path / "fpcalc"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"prints = {json.dumps(prints)}\n"
        "values = prints.get(os.path.basename(sys.argv[-1]))\n"
        "if not values:\n"
        "    print('ERROR: Empty fingerprint', file=sys.stderr); sys.exit(2)\n"
        "print(json.dumps({'duration': 1.0, 'fingerprint': values}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@needs_ffmpeg
def test_fix_11_titles_shared_with_a_sibling_episode_become_skip_zones(
    episode: Path, tmp_path: Path, stub_whisper: Path
) -> None:
    """ADR-0074 §2: the audio two episodes share near an end is titles, and is skipped."""
    import random

    rng = random.Random(7)
    theme = [rng.getrandbits(32) for _ in range(160)]  # ~20 s
    ours = theme + [rng.getrandbits(32) for _ in range(40)]
    theirs = [rng.getrandbits(32) for _ in range(30)] + theme
    folder = tmp_path / "series"
    folder.mkdir()
    ep1 = folder / "ep1.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:duration=120:size=64x36:rate=5",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=duration=120",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(ep1),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    (folder / "ep2.mp4").write_bytes(b"sibling")
    (folder / "notes.txt").write_text("not a video", encoding="utf-8")
    fpcalc = _stub_fpcalc(tmp_path, {"ep1.mp4": ours, "ep2.mp4": theirs})
    index = LocalFootageIndex(
        _settings(tmp_path), whisper=str(stub_whisper), fpcalc=str(fpcalc), timeout=120.0
    )
    footage = index.index(LocatedEpisode(source_ref="ep1.mp4", path=str(ep1)))
    assert len(footage.skips) == 1, "the shared theme, within the first quarter of 120 s"
    assert footage.skips[0].start_ms == 0
    assert abs(footage.skips[0].end_ms - 20_000) < 500


@needs_ffmpeg
def test_fix_11_an_episode_alone_in_its_folder_has_no_zones(
    episode: Path, tmp_path: Path, stub_whisper: Path
) -> None:
    folder = tmp_path / "alone"
    folder.mkdir()
    only = folder / "only.mp4"
    only.write_bytes(episode.read_bytes())
    missing = LocalFootageIndex(
        _settings(tmp_path), whisper=str(stub_whisper), fpcalc="fpcalc-not-installed"
    )
    footage = missing.index(LocatedEpisode(source_ref="only.mp4", path=str(only)))
    assert footage.skips == (), "no sibling means fpcalc is not even needed"


# --- 3. Refusals -------------------------------------------------------------------------


def test_fix_04_a_missing_episode_is_refused(tmp_path: Path, stub_whisper: Path) -> None:
    with pytest.raises(FootageIndexError, match="missing"):
        _index(tmp_path, stub_whisper).index(
            LocatedEpisode(source_ref="gone.mp4", path=str(tmp_path / "gone.mp4"))
        )


def test_fix_05_a_missing_binary_is_refused_by_name(tmp_path: Path, stub_whisper: Path) -> None:
    episode = tmp_path / "episode.mp4"
    episode.write_bytes(b"0")
    index = LocalFootageIndex(
        _settings(tmp_path), ffprobe="ffprobe-that-is-not-installed", whisper=str(stub_whisper)
    )
    with pytest.raises(FootageIndexError, match="ffprobe-that-is-not-installed"):
        index.index(LocatedEpisode(source_ref="e.mp4", path=str(episode)))


@needs_ffmpeg
def test_fix_06_a_whisper_that_writes_nothing_is_refused(episode: Path, tmp_path: Path) -> None:
    script = tmp_path / "whisper-cli"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    with pytest.raises(FootageIndexError, match="no transcript"):
        _index(tmp_path, script).index(LocatedEpisode(source_ref="episode.mp4", path=str(episode)))


@needs_ffmpeg
def test_fix_06_a_whisper_that_fails_is_refused(episode: Path, tmp_path: Path) -> None:
    script = tmp_path / "whisper-cli"
    script.write_text("#!/bin/sh\necho 'model load failed' >&2\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    with pytest.raises(FootageIndexError, match="could not transcribe"):
        _index(tmp_path, script).index(LocatedEpisode(source_ref="episode.mp4", path=str(episode)))


@needs_ffmpeg
def test_fix_06_a_transcript_that_is_not_json_is_refused(episode: Path, tmp_path: Path) -> None:
    script = tmp_path / "whisper-cli"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "argv = sys.argv\n"
        "open(argv[argv.index('-of') + 1] + '.json', 'w').write('not json')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    with pytest.raises(FootageIndexError, match="not JSON"):
        _index(tmp_path, script).index(LocatedEpisode(source_ref="episode.mp4", path=str(episode)))


# --- 4. Settings -------------------------------------------------------------------------


def test_fix_07_the_model_is_required_and_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FootageIndexError, match=MODEL_VAR):
        whisper_settings_from_env({})
    with pytest.raises(FootageIndexError, match="model is missing"):
        whisper_settings_from_env({MODEL_VAR: str(tmp_path / "nothing.bin")})


def test_fix_07_language_and_threshold_have_defaults(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(b"0")
    settings = whisper_settings_from_env({MODEL_VAR: str(model)})
    assert settings.language == "auto"
    assert settings.scene_threshold == 10.0

    tuned = whisper_settings_from_env(
        {MODEL_VAR: str(model), LANGUAGE_VAR: "ru", SCENE_THRESHOLD_VAR: "4.5"}
    )
    assert (tuned.language, tuned.scene_threshold) == ("ru", 4.5)

    with pytest.raises(FootageIndexError, match=SCENE_THRESHOLD_VAR):
        whisper_settings_from_env({MODEL_VAR: str(model), SCENE_THRESHOLD_VAR: "loud"})


# --- 5. The real thing, when a model is configured ---------------------------------------


@needs_ffmpeg
@pytest.mark.skipif(
    shutil.which("whisper-cli") is None or not os.environ.get(MODEL_VAR, "").strip(),
    reason=f"the real whisper-cli needs {MODEL_VAR} pointing at a downloaded GGML model",
)
def test_fix_08_the_real_whisper_runs_end_to_end(episode: Path) -> None:
    """Four seconds of a sine tone: it must return without inventing speech that is not there."""
    footage = LocalFootageIndex(whisper_settings_from_env(os.environ)).index(
        LocatedEpisode(source_ref="episode.mp4", path=str(episode))
    )
    assert abs(footage.duration_ms - 4_000) <= 250
    assert len(footage.scenes) == 1
    assert abs(footage.scenes[0].at_ms - 2_000) <= 100
    for span in footage.speech:
        assert 0 <= span.start_ms < span.end_ms <= footage.duration_ms
