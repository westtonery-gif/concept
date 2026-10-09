"""Setup tooling that needs no assistant (ADR-0093): ``ENV`` (.env loading) and ``DOC`` (doctor)."""

from __future__ import annotations

from pathlib import Path

import pytest

from omemo_content_factory.infrastructure.dotenv_file import load_dotenv
from omemo_content_factory.infrastructure.environment_check import (
    Probe,
    check_environment,
    department_ready,
)

# --- ENV ----------------------------------------------------------------------------------


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_env_01_plain_exported_quoted_and_commented_lines_load(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "# a comment\n"
        "A=1\n"
        "export B=two\n"
        'C="three four"\n'
        "D='five'\n"
        "E=six # trailing comment\n"
        "F=\n"
        "\n",
    )
    environ: dict[str, str] = {}
    load_dotenv(path, environ)
    assert environ == {"A": "1", "B": "two", "C": "three four", "D": "five", "E": "six", "F": ""}


def test_env_02_an_existing_variable_is_never_overridden(tmp_path: Path) -> None:
    path = _write(tmp_path, "A=from-file\nB=from-file\n")
    environ = {"A": "from-shell"}
    assert load_dotenv(path, environ) == ["B"]
    assert environ == {"A": "from-shell", "B": "from-file"}


def test_env_03_a_missing_file_is_no_error(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    assert load_dotenv(tmp_path / "nope.env", environ) == []
    assert environ == {}


def test_env_04_a_malformed_line_is_skipped_and_the_rest_load(tmp_path: Path) -> None:
    path = _write(tmp_path, "no equals sign\n=novalue\n1BAD=x\nBAD NAME=x\nGOOD=yes\n")
    environ: dict[str, str] = {}
    load_dotenv(path, environ)
    assert environ == {"GOOD": "yes"}


# --- DOC ----------------------------------------------------------------------------------

_FILTERS_WITH = " ... subtitles  V->V  Render text subtitles\n"
_FILTERS_WITHOUT = " ... scale  V->V  Scale the input video\n"


def _probe(
    *,
    binaries: tuple[str, ...] = ("ffmpeg", "ffprobe", "whisper-cli", "fpcalc"),
    files: tuple[str, ...] = ("/models/w.bin",),
    dirs: tuple[str, ...] = ("/episodes",),
    filters: str = _FILTERS_WITH,
    platform: str = "darwin",
    python: tuple[int, int] = (3, 12),
) -> Probe:
    return Probe(
        which=lambda name: f"/usr/bin/{name}" if name in binaries else None,
        ffmpeg_filters=lambda binary: filters,
        is_file=lambda path: path in files,
        is_dir=lambda path: path in dirs,
        platform=platform,
        python_version=python,
    )


_CLIPPING_ENV = {"OMEMO_WHISPER_MODEL": "/models/w.bin", "OMEMO_EPISODE_ROOT": "/episodes"}


def test_doc_01_a_fully_equipped_department_is_ready_and_one_gap_breaks_it() -> None:
    ok = check_environment(_CLIPPING_ENV, ["clipping"], probe=_probe())
    assert department_ready(ok, "clipping")
    broken = check_environment(_CLIPPING_ENV, ["clipping"], probe=_probe(binaries=("ffmpeg",)))
    assert not department_ready(broken, "clipping")


def test_doc_02_a_missing_optional_item_does_not_fail_a_department() -> None:
    checks = check_environment(
        {"OMEMO_WHISPER_MODEL": "/models/w.bin"},
        ["clipping"],
        probe=_probe(binaries=("ffmpeg", "ffprobe", "whisper-cli"), dirs=()),
    )
    assert department_ready(checks, "clipping")
    optional = [c for c in checks if not c.required]
    assert optional and any(not c.ok for c in optional)


def test_doc_03_a_value_is_never_part_of_the_report() -> None:
    secret = "sk-very-secret-value-123"
    checks = check_environment(
        {
            "OMEMO_UPLOAD_POST_API_KEY": secret,
            "OMEMO_PROVIDER__DEFAULT": "xai",
            "XAI_API_KEY": secret,
        },
        ["core", "posting"],
        probe=_probe(),
    )
    for check in checks:
        assert secret not in f"{check.name}{check.detail}{check.hint}"
    assert any(c.name == "OMEMO_UPLOAD_POST_API_KEY" and c.ok for c in checks)


def test_doc_04_an_ffmpeg_without_libass_is_a_named_failure_for_clipping() -> None:
    checks = check_environment(_CLIPPING_ENV, ["clipping"], probe=_probe(filters=_FILTERS_WITHOUT))
    (subtitles,) = [c for c in checks if "subtitles" in c.name]
    assert not subtitles.ok and subtitles.required
    assert "libass" in subtitles.hint
    assert not department_ready(checks, "clipping")


def test_doc_05_install_hints_follow_the_platform() -> None:
    mac = check_environment({}, ["clipping"], probe=_probe(binaries=(), platform="darwin"))
    linux = check_environment({}, ["clipping"], probe=_probe(binaries=(), platform="linux"))
    assert any("brew" in c.hint for c in mac if c.name.startswith("`whisper"))
    assert any("apt" in c.hint for c in linux if c.name.startswith("`ffmpeg`"))
    assert not any("brew" in c.hint for c in linux)


def test_doc_06_an_unknown_department_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown department"):
        check_environment({}, ["nope"], probe=_probe())


def test_doc_07_an_old_python_fails_core() -> None:
    checks = check_environment({}, ["core"], probe=_probe(python=(3, 9)))
    assert not department_ready(checks, "core")


def test_doc_08_core_asks_only_for_the_key_of_the_provider_the_configuration_selects() -> None:
    grok = check_environment({"OMEMO_PROVIDER__DEFAULT": "xai"}, ["core"], probe=_probe())
    names = {c.name for c in grok}
    assert "XAI_API_KEY" in names and "ANTHROPIC_API_KEY" not in names
    assert not department_ready(grok, "core")
    ready = check_environment(
        {"OMEMO_PROVIDER__DEFAULT": "xai", "XAI_API_KEY": "k"}, ["core"], probe=_probe()
    )
    assert department_ready(ready, "core")


def test_doc_08_no_provider_selected_is_a_named_failure_and_keyless_local_needs_no_key() -> None:
    none = check_environment({}, ["core"], probe=_probe())
    assert not department_ready(none, "core")
    assert any("OMEMO_PROVIDER__DEFAULT" in c.name for c in none)
    local = check_environment({"OMEMO_PROVIDER__DEFAULT": "ollama"}, ["core"], probe=_probe())
    assert department_ready(local, "core")
    claude = check_environment(
        {"OMEMO_PROVIDER__QA": "anthropic", "OMEMO_PROVIDER__X": "fake"}, ["core"], probe=_probe()
    )
    assert "ANTHROPIC_API_KEY" in {c.name for c in claude}


def test_doc_08_a_custom_endpoint_is_asked_for_the_key_variable_it_names() -> None:
    checks = check_environment(
        {
            "OMEMO_PROVIDER__DEFAULT": "openai-compatible",
            "OMEMO_API_KEY_ENV__DEFAULT": "MY_GATEWAY_KEY",
        },
        ["core"],
        probe=_probe(),
    )
    assert "MY_GATEWAY_KEY" in {c.name for c in checks}


def test_env_05_a_managed_block_is_replaced_in_place_and_the_rest_is_untouched(
    tmp_path: Path,
) -> None:
    from omemo_content_factory.infrastructure.dotenv_file import replace_block

    path = _write(tmp_path, "# mine\nKEEP=1\n")
    replace_block(path, "llm", ["A=1", "B=2"])
    replace_block(path, "llm", ["A=9"])
    text = path.read_text(encoding="utf-8")
    assert text.count("# >>> llm") == 1 and "A=9" in text and "A=1" not in text
    assert text.startswith("# mine\nKEEP=1\n")
    environ: dict[str, str] = {}
    load_dotenv(path, environ)
    assert environ == {"KEEP": "1", "A": "9"}
    created = tmp_path / "new.env"
    replace_block(created, "llm", ["X=1"])
    assert created.read_text(encoding="utf-8") == "# >>> llm\nX=1\n# <<< llm\n"


# --- the model block (ADR-0094 §5) ------------------------------------------------------------


def test_llm_01_a_written_block_is_selectable_for_every_role(tmp_path: Path) -> None:
    from omemo_content_factory.infrastructure.dotenv_file import replace_block
    from omemo_content_factory.infrastructure.llm_setup import (
        BLOCK_NAME,
        LLMChoice,
        env_block,
        key_variable,
        problem_with,
    )
    from omemo_content_factory.infrastructure.openai_compatible_llm import (
        OpenAICompatibleLLMClient,
    )
    from omemo_content_factory.infrastructure.provider_model import client_for_role

    choice = LLMChoice("xai", "grok-4", "3", "15")
    assert problem_with(choice) is None and key_variable(choice) == "XAI_API_KEY"
    path = _write(tmp_path, "KEEP=1\n")
    replace_block(path, BLOCK_NAME, env_block(choice))
    environ: dict[str, str] = {"XAI_API_KEY": "k"}
    load_dotenv(path, environ)
    for role in ("qa_agent@v1", "story_writer@v1", "clip_post_writer@v1"):
        client = client_for_role(role, environ)
        assert isinstance(client, OpenAICompatibleLLMClient) and client.model == "grok-4"
    assert environ["KEEP"] == "1"
    assert "XAI_API_KEY=..." in "\n".join(env_block(choice))


def test_llm_02_a_bad_choice_is_reported_before_anything_is_written() -> None:
    from omemo_content_factory.infrastructure.llm_setup import LLMChoice, problem_with

    assert "base URL" in (problem_with(LLMChoice("openai-compatible", "m", "1", "2")) or "")
    assert "Anthropic" in (problem_with(LLMChoice("xai", "m", "1", "2", thinking="adaptive")) or "")
    assert "pricing" in (problem_with(LLMChoice("xai", "m", "abc", "2")) or "")
    assert problem_with(LLMChoice("ollama", "llama3.1", "0", "0")) is None
    custom = LLMChoice(
        "openai-compatible", "m", "1", "2", base_url="https://x.example/v1", key_env="MY_KEY"
    )
    assert problem_with(custom) is None
    from omemo_content_factory.infrastructure.llm_setup import key_variable

    assert key_variable(custom) == "MY_KEY"
