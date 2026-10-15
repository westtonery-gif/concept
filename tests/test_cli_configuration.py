"""Configuration failures must explain how to configure the command that actually failed."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    "PROVIDER",
    "MODEL",
    "INPUT_PRICE_PER_MILLION",
    "OUTPUT_PRICE_PER_MILLION",
    "PRICE_CURRENCY",
    "MAX_TOKENS",
    "THINKING",
)


@pytest.mark.parametrize("post_writer", [False, True])
@pytest.mark.parametrize("moment_picker", [False, True])
def test_clipping_configuration_help_names_only_enabled_clipping_roles(
    post_writer: bool, moment_picker: bool
) -> None:
    environ = {key: value for key, value in os.environ.items() if not key.startswith("OMEMO_")}
    if post_writer:
        environ["OMEMO_PROVIDER__CLIP_POST_WRITER_V1"] = "anthropic"
    if moment_picker:
        environ["OMEMO_PROVIDER__CLIP_MOMENT_PICKER_V1"] = "anthropic"
    result = subprocess.run(
        [sys.executable, "demo_clips.py", "episode-id"],
        cwd=ROOT,
        env=environ,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 1, result.stderr
    for field in FIELDS:
        assert f"OMEMO_{field}__CLIP_QA_AGENT_V1=" in result.stdout
        assert (f"OMEMO_{field}__CLIP_POST_WRITER_V1=" in result.stdout) is post_writer
        assert (f"OMEMO_{field}__CLIP_MOMENT_PICKER_V1=" in result.stdout) is moment_picker
    assert "CONTENT_RESEARCHER_V1" not in result.stdout
    assert "SCRIPT_WRITER_V1" not in result.stdout
    assert "OMEMO_PROVIDER__QA_AGENT_V1=" not in result.stdout


def test_factory_configuration_help_includes_the_required_request_budget() -> None:
    environ = {key: value for key, value in os.environ.items() if not key.startswith("OMEMO_")}
    environ["ANTHROPIC_API_KEY"] = "unused-test-key"
    result = subprocess.run(
        [sys.executable, "demo_factory.py"],
        cwd=ROOT,
        env=environ,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    for role in ("CONTENT_RESEARCHER_V1", "SCRIPT_WRITER_V1", "QA_AGENT_V1"):
        for field in FIELDS:
            assert f"OMEMO_{field}__{role}=" in result.stdout


def test_configured_publisher_does_not_enable_posting_without_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT))
    from importlib import import_module

    _build_posting = import_module("demo_clips")._build_posting

    from omemo_content_factory.composition import CompositionError

    env = {"OMEMO_INSTAGRAM_USER_ID": "1784"}
    assert _build_posting(env) is None
    with pytest.raises(CompositionError, match="OMEMO_POSTING_PROVIDER"):
        _build_posting(env, enabled=True)
    env["OMEMO_POSTING_PROVIDER"] = "instagram"
    assert _build_posting(env, enabled=True) is not None
