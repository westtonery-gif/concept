"""Tests for ``ElevenLabsSpeechSynthesizer`` (GENERATION_ACCEPTANCE §8, ELV; ADR-0086).

The adapter's real ``urllib`` code talks over a real socket to a local server that plays
ElevenLabs' ``/v1/text-to-speech/{voice_id}/with-timestamps``: it records each request as it
arrived and answers with a scripted body whose audio is real PCM. Nothing is mocked below the
adapter and nothing leaves ``127.0.0.1``. The contract was read from the vendor's own SDK; only a
live call proves it against the service.
"""

from __future__ import annotations

import base64
import itertools
import json
import math
import os
import threading
import wave
from array import array
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from omemo_content_factory.adapters.speech_synthesizer import (
    SpeechRequest,
    SpeechSynthesizer,
    SpeechSynthesizerError,
)
from omemo_content_factory.infrastructure.elevenlabs_speech_synthesizer import (
    API_KEY_VAR,
    MODEL_VAR,
    STABILITY_VAR,
    ElevenLabsSettings,
    ElevenLabsSpeechSynthesizer,
    elevenlabs_settings_from_env,
    words_from_alignment,
)

KEY = "elevenlabs-secret-key-value"
SETTINGS = ElevenLabsSettings(api_key=KEY, model="eleven_v3")
RATE = 24_000


def pcm(milliseconds: int) -> bytes:
    count = RATE * milliseconds // 1000
    return array("h", (round(9000 * math.sin(i / 6)) for i in range(count))).tobytes()


def alignment(text: str, per_char: float = 0.05, start: float = 0.1) -> dict[str, Any]:
    """A vendor-shaped alignment: every character takes ``per_char`` seconds."""
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(start + i * per_char, 3) for i in range(len(text))],
        "character_end_times_seconds": [
            round(start + (i + 1) * per_char, 3) for i in range(len(text))
        ],
    }


def answer(text: str, milliseconds: int = 1500, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "audio_base64": base64.b64encode(pcm(milliseconds)).decode("ascii"),
        "alignment": alignment(text),
        "normalized_alignment": alignment(text),
    }
    body.update(extra)
    return body


@dataclass
class FakeElevenLabs:
    answer: tuple[int, Any] = (200, None)
    requests: list[tuple[str, dict[str, str], Any]] = field(default_factory=list)
    truncate_next: int = 0  # how many answers to cut short before answering properly


@pytest.fixture
def elevenlabs() -> Iterator[tuple[FakeElevenLabs, str]]:
    fake = FakeElevenLabs()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            fake.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            code, payload = fake.answer
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if fake.truncate_next > 0:
                fake.truncate_next -= 1
                self.wfile.write(raw[: len(raw) // 2])  # the body ends before its length says
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(raw)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _request(tmp_path: Path, text: str = "Hello there, friend.", **overrides: Any) -> SpeechRequest:
    values: dict[str, Any] = {
        "text": text,
        "voice": "voice-abc123",
        "destination": str(tmp_path / "out" / "line.wav"),
    }
    values.update(overrides)
    return SpeechRequest(**values)


def _synth(url: str, settings: ElevenLabsSettings = SETTINGS) -> ElevenLabsSpeechSynthesizer:
    return ElevenLabsSpeechSynthesizer(settings, api_url=url, timeout=5.0, retry_delay=0.0)


def test_elv_01_one_post_per_line_and_the_file_is_measured(
    tmp_path: Path, elevenlabs: tuple[FakeElevenLabs, str]
) -> None:
    fake, url = elevenlabs
    fake.answer = (200, answer("Hello there, friend.", 1500))
    request = _request(tmp_path)
    speech = _synth(url).synthesize(request)
    ((path, headers, body),) = fake.requests
    assert path == "/v1/text-to-speech/voice-abc123/with-timestamps?output_format=pcm_24000"
    assert headers["xi-api-key"] == KEY
    assert body == {"text": "Hello there, friend.", "model_id": "eleven_v3"}
    with wave.open(request.destination, "rb") as written:
        assert (written.getnchannels(), written.getsampwidth(), written.getframerate()) == (
            1,
            2,
            RATE,
        )
        frames = written.getnframes()
    assert speech.duration_ms == round(frames * 1000 / RATE) == 1500
    assert speech.sample_rate == RATE
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["line.wav"]


def test_elv_02_words_are_the_vendors_own_times(
    tmp_path: Path, elevenlabs: tuple[FakeElevenLabs, str]
) -> None:
    fake, url = elevenlabs
    fake.answer = (
        200,
        answer("Hi there"),
    )  # 8 characters x 50 ms from 100 ms; "there" starts at the 4th
    speech = _synth(url).synthesize(_request(tmp_path, "Hi there"))
    assert [(w.text, w.start_ms, w.end_ms) for w in speech.words] == [
        ("Hi", 100, 200),
        ("there", 250, 500),
    ]


def test_elv_03_an_audio_tag_is_not_a_word() -> None:
    text = "[laughs] Ha, you wish [whispers harder] ok"
    words = words_from_alignment(list(text), *_times(text), limit_ms=60_000)
    assert [w.text for w in words] == ["Ha,", "you", "wish", "ok"]
    glued = "Hey[sighs]you"
    assert [w.text for w in words_from_alignment(list(glued), *_times(glued), limit_ms=60_000)] == [
        "Hey",
        "you",
    ]


def _times(text: str) -> tuple[list[float], list[float]]:
    data = alignment(text)
    return data["character_start_times_seconds"], data["character_end_times_seconds"]


def test_elv_03_a_lone_dash_is_not_a_word_and_words_stay_inside_the_file() -> None:
    text = "Wait — what?"
    words = words_from_alignment(list(text), *_times(text), limit_ms=700)
    assert [w.text for w in words] == ["Wait", "what?"]
    assert all(0 <= w.start_ms < w.end_ms <= 700 for w in words)
    for earlier, later in itertools.pairwise(words):
        assert earlier.end_ms <= later.start_ms


def test_elv_04_speed_and_stability_go_into_voice_settings_only_when_set(
    tmp_path: Path, elevenlabs: tuple[FakeElevenLabs, str]
) -> None:
    fake, url = elevenlabs
    fake.answer = (200, answer("Hello there, friend."))
    _synth(url, ElevenLabsSettings(KEY, "eleven_v3", stability=0.5)).synthesize(
        _request(tmp_path, speed=1.1)
    )
    assert fake.requests[0][2]["voice_settings"] == {"stability": 0.5, "speed": 1.1}


def test_elv_05_a_voice_id_cannot_change_the_path(
    tmp_path: Path, elevenlabs: tuple[FakeElevenLabs, str]
) -> None:
    fake, url = elevenlabs
    fake.answer = (200, answer("Hello there, friend."))
    _synth(url).synthesize(_request(tmp_path, voice="../../v1/user"))
    assert fake.requests[0][0].startswith("/v1/text-to-speech/..%2F..%2Fv1%2Fuser/with-timestamps")


@pytest.mark.parametrize(
    "payload",
    [
        {"alignment": alignment("Hello there")},  # no audio
        {"audio_base64": "!!!not base64!!!", "alignment": alignment("Hello there")},
        {"audio_base64": base64.b64encode(pcm(500)).decode("ascii")},  # no alignment
        {
            "audio_base64": base64.b64encode(pcm(500)).decode("ascii"),
            "alignment": {"characters": ["a"], "character_start_times_seconds": []},
        },
        {
            "audio_base64": base64.b64encode(b"\x00\x00" * 4000).decode("ascii"),
            "alignment": alignment("Hello there"),
        },  # silence
    ],
)
def test_elv_06_a_malformed_answer_writes_nothing(
    tmp_path: Path, elevenlabs: tuple[FakeElevenLabs, str], payload: dict[str, Any]
) -> None:
    fake, url = elevenlabs
    fake.answer = (200, payload)
    with pytest.raises(SpeechSynthesizerError):
        _synth(url).synthesize(_request(tmp_path))
    assert not (tmp_path / "out" / "line.wav").exists()


def test_elv_06_a_non_json_or_non_object_answer_is_an_error(
    tmp_path: Path, elevenlabs: tuple[FakeElevenLabs, str]
) -> None:
    fake, url = elevenlabs
    fake.answer = (200, b"<html>")
    with pytest.raises(SpeechSynthesizerError, match="not JSON"):
        _synth(url).synthesize(_request(tmp_path))
    fake.answer = (200, ["a"])
    with pytest.raises(SpeechSynthesizerError, match="unexpected shape"):
        _synth(url).synthesize(_request(tmp_path))


def test_elv_07_a_refusal_carries_the_code_and_the_vendors_message_but_never_the_key(
    tmp_path: Path, elevenlabs: tuple[FakeElevenLabs, str]
) -> None:
    fake, url = elevenlabs
    fake.answer = (401, {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}})
    with pytest.raises(SpeechSynthesizerError) as raised:
        _synth(url).synthesize(_request(tmp_path))
    assert "HTTP 401" in str(raised.value) and "Invalid API key" in str(raised.value)
    assert KEY not in str(raised.value)
    fake.answer = (422, {"detail": [{"msg": "bad"}]})
    with pytest.raises(SpeechSynthesizerError, match="HTTP 422"):
        _synth(url).synthesize(_request(tmp_path))


def test_elv_11_a_body_cut_short_is_asked_for_again(
    tmp_path: Path, elevenlabs: tuple[FakeElevenLabs, str]
) -> None:
    fake, url = elevenlabs
    fake.answer = (200, answer("Hello there, friend."))
    fake.truncate_next = 2
    speech = _synth(url).synthesize(_request(tmp_path))
    assert len(fake.requests) == 3 and speech.duration_ms == 1500
    fake.truncate_next = 3  # more than the retries allow
    with pytest.raises(SpeechSynthesizerError, match="could not be reached"):
        _synth(url).synthesize(_request(tmp_path))


def test_elv_11_a_refusal_is_final_and_never_asked_again(
    tmp_path: Path, elevenlabs: tuple[FakeElevenLabs, str]
) -> None:
    fake, url = elevenlabs
    fake.answer = (401, {"detail": {"message": "Invalid API key"}})
    with pytest.raises(SpeechSynthesizerError, match="HTTP 401"):
        _synth(url).synthesize(_request(tmp_path))
    assert len(fake.requests) == 1


def test_elv_08_an_unreachable_service_is_named(tmp_path: Path) -> None:
    with pytest.raises(SpeechSynthesizerError, match="could not be reached"):
        _synth("http://127.0.0.1:9").synthesize(_request(tmp_path))


def test_elv_09_settings_from_the_environment() -> None:
    with pytest.raises(SpeechSynthesizerError) as missing:
        elevenlabs_settings_from_env({})
    assert API_KEY_VAR in str(missing.value) and MODEL_VAR in str(missing.value)
    environ = {API_KEY_VAR: KEY, MODEL_VAR: "eleven_v3"}
    assert elevenlabs_settings_from_env(environ).stability is None
    assert elevenlabs_settings_from_env({**environ, STABILITY_VAR: "0.5"}).stability == 0.5
    for bad in ("loud", "1.5", "-1"):
        with pytest.raises(SpeechSynthesizerError, match=STABILITY_VAR):
            elevenlabs_settings_from_env({**environ, STABILITY_VAR: bad})
    assert KEY not in repr(elevenlabs_settings_from_env(environ))


def test_elv_09_the_synthesizer_satisfies_the_port() -> None:
    synthesizer: SpeechSynthesizer = ElevenLabsSpeechSynthesizer(SETTINGS)
    assert synthesizer is not None


@pytest.mark.skipif(
    not (
        os.environ.get(API_KEY_VAR)
        and os.environ.get(MODEL_VAR)
        and os.environ.get("OMEMO_ELEVENLABS_LIVE_VOICE")
    ),
    reason="set OMEMO_ELEVENLABS_API_KEY, OMEMO_ELEVENLABS_MODEL and OMEMO_ELEVENLABS_LIVE_VOICE",
)
def test_liv_04_the_real_service_speaks_and_a_tag_is_not_a_word(tmp_path: Path) -> None:
    synthesizer = ElevenLabsSpeechSynthesizer(elevenlabs_settings_from_env(os.environ))
    speech = synthesizer.synthesize(
        SpeechRequest(
            text="[laughs] Every morning? For a few sad sprouts?",
            voice=os.environ["OMEMO_ELEVENLABS_LIVE_VOICE"],
            destination=str(tmp_path / "live.wav"),
        )
    )
    assert speech.duration_ms > 1000
    assert next(word.text for word in speech.words) == "Every"
