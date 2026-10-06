"""Tests for the local Kokoro ``SpeechSynthesizer`` (GENERATION_ACCEPTANCE §7, KOK; ADR-0085).

The engine is a seam, so everything that is ours — the voice grammar, the file, its measurements,
the word times — is tested with a generated tone and no model. One live test runs the real model
when ``OMEMO_KOKORO_MODEL`` / ``OMEMO_KOKORO_VOICES`` are set and the packages are installed, and
skips with its reason otherwise.
"""

from __future__ import annotations

import itertools
import math
import os
import wave
from array import array
from pathlib import Path

import pytest

from omemo_content_factory.adapters.speech_synthesizer import (
    SpeechRequest,
    SpeechSynthesizer,
    SpeechSynthesizerError,
)
from omemo_content_factory.composition import CompositionError, build_speech_synthesizer
from omemo_content_factory.infrastructure.elevenlabs_speech_synthesizer import (
    ElevenLabsSpeechSynthesizer,
)
from omemo_content_factory.infrastructure.kokoro_speech_synthesizer import (
    MODEL_VAR,
    VOICES_VAR,
    KokoroSpeechSynthesizer,
    VoicePart,
    estimate_word_times,
    kokoro_settings_from_env,
    parse_voice,
)

RATE = 24_000


def tone(milliseconds: int, amplitude: int = 12_000) -> array[int]:
    count = RATE * milliseconds // 1000
    return array("h", (round(amplitude * math.sin(i / 7)) for i in range(count)))


def silence(milliseconds: int) -> array[int]:
    return array("h", [0] * (RATE * milliseconds // 1000))


class FakeEngine:
    """Leading silence, a tone, trailing silence — and a record of what it was asked."""

    def __init__(
        self,
        *,
        lead_ms: int = 200,
        speech_ms: int = 1000,
        tail_ms: int = 300,
        fail: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[VoicePart, ...], float]] = []
        self._shape = (lead_ms, speech_ms, tail_ms)
        self._fail = fail

    def render(self, text: str, voice: tuple[VoicePart, ...], speed: float, /) -> tuple[bytes, int]:
        self.calls.append((text, voice, speed))
        if self._fail is not None:
            raise self._fail
        lead, speech, tail = self._shape
        samples = silence(lead) + tone(speech) + silence(tail)
        return samples.tobytes(), RATE


def say(
    tmp_path: Path,
    engine: FakeEngine,
    text: str = "Look at this dry dirt, farmer.",
    **extra: object,
) -> tuple[SpeechRequest, object]:
    request = SpeechRequest(
        text=text, voice=str(extra.get("voice", "am_michael")), destination=str(tmp_path / "a.wav")
    )
    return request, KokoroSpeechSynthesizer(engine=engine).synthesize(request)


def test_kok_01_the_measurements_come_from_the_written_file(tmp_path: Path) -> None:
    engine = FakeEngine(lead_ms=200, speech_ms=1000, tail_ms=300)
    request, speech = say(tmp_path, engine)
    with wave.open(request.destination, "rb") as written:
        assert (written.getnchannels(), written.getsampwidth()) == (1, 2)
        assert written.getframerate() == RATE
        frames = written.getnframes()
    assert speech.sample_rate == RATE  # type: ignore[attr-defined]
    assert speech.duration_ms == round(frames * 1000 / RATE) == 1500  # type: ignore[attr-defined]
    assert speech.path == request.destination  # type: ignore[attr-defined]


def test_kok_02_words_are_ordered_inside_the_file_and_hug_the_sound(tmp_path: Path) -> None:
    text = 'Look at this dry dirt, farmer. "Never!"'
    _, speech = say(tmp_path, FakeEngine(lead_ms=200, speech_ms=1000, tail_ms=300), text)
    words = speech.words  # type: ignore[attr-defined]
    assert [word.text for word in words] == text.split()
    for earlier, later in itertools.pairwise(words):
        assert earlier.end_ms <= later.start_ms
    assert all(0 <= word.start_ms < word.end_ms <= speech.duration_ms for word in words)  # type: ignore[attr-defined]
    # sound runs 200..1200 ms of a 1500 ms file; the words sit on it, not on the silence
    assert 150 <= words[0].start_ms <= 220
    assert 1180 <= words[-1].end_ms <= 1250


def test_kok_02_a_longer_word_gets_a_longer_span_and_a_pause_follows_a_comma() -> None:
    words = estimate_word_times("a, extraordinarily b", 0, 1000, limit_ms=1000)
    spans = [word.end_ms - word.start_ms for word in words]
    assert spans[1] > spans[0] and spans[1] > spans[2]
    assert words[1].start_ms - words[0].end_ms > words[2].start_ms - words[1].end_ms  # comma pause


def test_kok_02_a_lone_dash_is_not_a_word() -> None:
    words = estimate_word_times("Wait — what?", 0, 900, limit_ms=900)
    assert [word.text for word in words] == ["Wait", "what?"]


def test_kok_03_a_blend_reaches_the_engine_normalised(tmp_path: Path) -> None:
    for spec in ("am_michael:0.6+am_onyx:0.4", "am_michael:3+am_onyx:2"):
        engine = FakeEngine()
        say(tmp_path, engine, voice=spec)
        _, voice, _ = engine.calls[0]
        assert [part.name for part in voice] == ["am_michael", "am_onyx"]
        assert [round(part.weight, 6) for part in voice] == [0.6, 0.4]
    assert parse_voice("bf_emma") == (VoicePart("bf_emma", 1.0),)


@pytest.mark.parametrize(
    "spec",
    ["+am_adam", "am_adam+am_adam", "am_adam:0", "am_adam:-1", "am_adam:x", "am_adam:inf"],
)
def test_kok_04_a_bad_voice_is_refused_before_the_engine(tmp_path: Path, spec: str) -> None:
    engine = FakeEngine()
    request = SpeechRequest(text="Hi", voice=spec, destination=str(tmp_path / "a.wav"))
    with pytest.raises(SpeechSynthesizerError):
        KokoroSpeechSynthesizer(engine=engine).synthesize(request)
    assert engine.calls == []


def test_kok_04_an_empty_voice_name_is_refused() -> None:
    with pytest.raises(SpeechSynthesizerError):
        parse_voice(":0.5")


@pytest.mark.parametrize("voice", ["zf_xiaobei", "ef_dora", "hf_alpha"])
def test_kok_05_only_english_voices_are_spoken_in_v1(tmp_path: Path, voice: str) -> None:
    engine = FakeEngine()
    with pytest.raises(SpeechSynthesizerError, match="English"):
        say(tmp_path, engine, voice=voice)
    assert engine.calls == []


def test_kok_06_an_engine_failure_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "a.wav"
    target.write_bytes(b"previous")
    with pytest.raises(SpeechSynthesizerError, match="boom"):
        say(tmp_path, FakeEngine(fail=RuntimeError("boom")))
    assert target.read_bytes() == b"previous"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.wav"]


@pytest.mark.parametrize("engine", [FakeEngine(lead_ms=100, speech_ms=0, tail_ms=100)])
def test_kok_06_silence_is_an_error_not_an_empty_file(tmp_path: Path, engine: FakeEngine) -> None:
    with pytest.raises(SpeechSynthesizerError, match="silence"):
        say(tmp_path, engine)
    assert list(tmp_path.iterdir()) == []


def test_kok_07_repeating_overwrites_and_leaves_no_temporary_files(tmp_path: Path) -> None:
    say(tmp_path, FakeEngine(speech_ms=500))
    _, second = say(tmp_path, FakeEngine(speech_ms=1500))
    assert second.duration_ms == 2000  # type: ignore[attr-defined]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.wav"]


def test_kok_08_settings_need_both_variables_and_both_files(tmp_path: Path) -> None:
    with pytest.raises(SpeechSynthesizerError) as missing:
        kokoro_settings_from_env({})
    assert MODEL_VAR in str(missing.value) and VOICES_VAR in str(missing.value)
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    with pytest.raises(SpeechSynthesizerError) as half:
        kokoro_settings_from_env({MODEL_VAR: str(model)})
    assert VOICES_VAR in str(half.value) and MODEL_VAR not in str(half.value)
    with pytest.raises(SpeechSynthesizerError, match="missing"):
        kokoro_settings_from_env({MODEL_VAR: str(model), VOICES_VAR: str(tmp_path / "nope.bin")})
    voices = tmp_path / "v.bin"
    voices.write_bytes(b"x")
    settings = kokoro_settings_from_env({MODEL_VAR: str(model), VOICES_VAR: str(voices)})
    assert (settings.model, settings.voices) == (model, voices)


def test_kok_08_the_synthesizer_satisfies_the_port() -> None:
    synthesizer: SpeechSynthesizer = KokoroSpeechSynthesizer(engine=FakeEngine())
    assert synthesizer is not None


def test_kok_09_the_root_builds_it_without_loading_a_model(tmp_path: Path) -> None:
    with pytest.raises(CompositionError) as missing:
        build_speech_synthesizer({})
    assert MODEL_VAR in str(missing.value) and VOICES_VAR in str(missing.value)
    model, voices = tmp_path / "m.onnx", tmp_path / "v.bin"
    model.write_bytes(b"not a model")
    voices.write_bytes(b"not voices")
    built = build_speech_synthesizer({MODEL_VAR: str(model), VOICES_VAR: str(voices)})
    assert isinstance(built, KokoroSpeechSynthesizer)


def test_elv_10_the_root_picks_a_vendor_by_presence_and_refuses_two(tmp_path: Path) -> None:
    key = {"OMEMO_ELEVENLABS_API_KEY": "k", "OMEMO_ELEVENLABS_MODEL": "eleven_v3"}
    assert isinstance(build_speech_synthesizer(key), ElevenLabsSpeechSynthesizer)
    with pytest.raises(CompositionError, match="OMEMO_ELEVENLABS_MODEL"):
        build_speech_synthesizer({"OMEMO_ELEVENLABS_API_KEY": "k"})
    kokoro = {MODEL_VAR: str(tmp_path / "m"), VOICES_VAR: str(tmp_path / "v")}
    with pytest.raises(CompositionError, match="twice"):
        build_speech_synthesizer({**key, **kokoro})
    with pytest.raises(CompositionError) as neither:
        build_speech_synthesizer({})
    assert "OMEMO_ELEVENLABS_API_KEY" in str(neither.value) and MODEL_VAR in str(neither.value)


@pytest.mark.skipif(
    not (os.environ.get(MODEL_VAR) and os.environ.get(VOICES_VAR)),
    reason=f"{MODEL_VAR} and {VOICES_VAR} are not set",
)
def test_liv_03_the_real_model_speaks(tmp_path: Path) -> None:
    pytest.importorskip("kokoro_onnx")
    synthesizer = KokoroSpeechSynthesizer(kokoro_settings_from_env(os.environ))
    speech = synthesizer.synthesize(
        SpeechRequest(
            text="Every morning, sir. I will water it every single morning.",
            voice="af_heart:0.5+af_nova:0.5",
            destination=str(tmp_path / "live.wav"),
        )
    )
    assert speech.duration_ms > 1500
    assert next(word.text for word in speech.words) == "Every"
