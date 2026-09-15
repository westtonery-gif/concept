"""PST acceptance: versioned Prompt data is external and loaded only at composition time."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

import pytest

import omemo_content_factory.composition as composition
from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.skill_execution import SkillPreprocessingTaskExecutor
from omemo_content_factory.composition import (
    CompositionError,
    build_content_director,
    build_executor_map,
    load_prompt_catalogue,
)
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.prompt import Prompt, PromptVersion
from omemo_content_factory.domain.schema import Schema, SchemaVersion
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.llm import LLMCompletion, LLMTaskExecutor
from omemo_content_factory.tools.toolbox import Toolbox

_RIN_SYSTEM = (
    "Ты Content Researcher видео-фабрики OMEMO. По присланному брифу кратко определи целевую "
    "аудиторию (audience) и контентный угол подачи (angle). Минимально, по делу, без воды."
)
_LEO_SYSTEM = (
    "Ты Script Writer видео-фабрики OMEMO. По присланному ресёрчу напиши короткий вертикальный "
    "видео-сценарий на русском: цепляющий заголовок (title), хук первых секунд (hook) и сценарий "
    "по сценам с призывом к действию (script). Кратко, по делу, без воды."
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "prompts.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _valid_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "prompt_id": "p",
        "version": 1,
        "schema_ref": "s@v1",
        "system": "system",
        "user_template": "input: {input}",
    }
    record.update(overrides)
    return record


def _toml(record: dict[str, object]) -> str:
    lines = ["[[prompts]]"]
    for key, value in record.items():
        if isinstance(value, str):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            lines.append(f'{key} = "{escaped}"')
        elif isinstance(value, bool):
            lines.append(f"{key} = {str(value).lower()}")
        else:
            lines.append(f"{key} = {value}")
    return "\n".join(lines)


class _NeverCalledClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox
    ) -> LLMCompletion:
        self.calls += 1
        raise AssertionError("model called during composition")


def test_pst_01_bundled_catalogue_preserves_both_prompts_exactly() -> None:
    prompts = load_prompt_catalogue()

    assert set(prompts) == {rin.PROMPT_REF, leo.PROMPT_REF}
    assert prompts[rin.PROMPT_REF] == Prompt(
        prompt_id="content-researcher",
        version=PromptVersion(1),
        schema_ref="content-research-report",
        system=_RIN_SYSTEM,
        user_template="Бриф и контекст:\n{input}\n\nОпредели: audience, angle.",
    )
    assert prompts[leo.PROMPT_REF] == Prompt(
        prompt_id="script-writer",
        version=PromptVersion(1),
        schema_ref="script-draft@v1",
        system=_LEO_SYSTEM,
        user_template="Ресёрч и контекст:\n{input}\n\nСделай сценарий: title, hook, script.",
    )


def test_pst_02_role_modules_do_not_construct_or_own_prompt_text() -> None:
    for role in (rin, leo):
        source = inspect.getsource(role)
        assert "Prompt(" not in source
        assert "system=" not in source
        assert "user_template=" not in source
        assert not hasattr(role, "PROMPTS")


def test_pst_03_default_store_is_injected_with_exact_version_ref() -> None:
    configured = build_executor_map(
        rin.AGENTS,
        None,
        FakeLLMClient(),
        rin.SCHEMAS,
        skill_invocations=rin.SKILL_INVOCATIONS,
    )[rin.AGENT_REF]
    assert isinstance(configured, SkillPreprocessingTaskExecutor)
    executor = configured.delegate
    assert isinstance(executor, LLMTaskExecutor)
    assert executor.system_prompt == _RIN_SYSTEM
    assert executor.user_template == "Бриф и контекст:\n{input}\n\nОпредели: audience, angle."
    assert executor.prompt_ref == "content-researcher@v1"


def test_pst_04_top_level_build_reads_one_catalogue_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    prompt = load_prompt_catalogue()[leo.PROMPT_REF]

    def counted_load(path: Path | None = None) -> dict[str, Prompt]:
        nonlocal calls
        assert path is None
        calls += 1
        return {prompt.prompt_id: prompt}

    monkeypatch.setattr(composition, "load_prompt_catalogue", counted_load)
    client = _NeverCalledClient()
    build_content_director(leo.AGENTS, None, client, leo.SCHEMAS)

    assert calls == 1
    assert client.calls == 0


def test_pst_05_explicit_mapping_does_not_read_bundled_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = Prompt("p", PromptVersion(1), "system", "{input}", "s@v1")
    agent = Agent("a@v1", "A", prompt.prompt_id)
    schema = Schema.create(
        schema_id="s",
        version=SchemaVersion(1),
        description="schema",
        required_fields=("field",),
    )

    def forbidden_load(path: Path | None = None) -> dict[str, Prompt]:
        raise AssertionError(f"unexpected store read: {path}")

    monkeypatch.setattr(composition, "load_prompt_catalogue", forbidden_load)
    executors = build_executor_map(
        [agent], {prompt.prompt_id: prompt}, FakeLLMClient(), {"s@v1": schema}
    )

    assert set(executors) == {agent.agent_id}


@pytest.mark.parametrize("text", ["not = [valid", "\xff"])
def test_pst_06_invalid_toml_or_utf8_fails_closed(tmp_path: Path, text: str) -> None:
    path = tmp_path / "invalid.toml"
    if text == "\xff":
        path.write_bytes(b"\xff")
    else:
        path.write_text(text, encoding="utf-8")

    with pytest.raises(CompositionError):
        load_prompt_catalogue(path)


def test_pst_06_unreadable_path_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(CompositionError, match="cannot read"):
        load_prompt_catalogue(tmp_path / "missing.toml")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "other = 1",
        "prompts = []",
        "prompts = [1]",
        _toml({key: value for key, value in _valid_record().items() if key != "system"}),
        _toml(_valid_record(extra="unexpected")),
    ],
)
def test_pst_07_invalid_catalogue_shape_fails_closed(tmp_path: Path, text: str) -> None:
    with pytest.raises(CompositionError):
        load_prompt_catalogue(_write(tmp_path, text))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_id", " "),
        ("schema_ref", ""),
        ("system", "\t"),
        ("user_template", "\n"),
        ("version", True),
        ("version", 0),
        ("version", -1),
        ("version", "1"),
    ],
)
def test_pst_08_invalid_values_fail_closed(tmp_path: Path, field: str, value: object) -> None:
    with pytest.raises(CompositionError):
        load_prompt_catalogue(_write(tmp_path, _toml(_valid_record(**{field: value}))))


def test_pst_09_duplicate_prompt_id_fails_closed(tmp_path: Path) -> None:
    record = _toml(_valid_record())
    with pytest.raises(CompositionError, match="duplicate prompt_id"):
        load_prompt_catalogue(_write(tmp_path, f"{record}\n{record}\n"))


def test_pst_10_catalogue_is_a_bundled_package_resource() -> None:
    resource = resources.files(composition.PROMPT_CATALOGUE_PACKAGE).joinpath(
        composition.PROMPT_CATALOGUE_RESOURCE
    )
    assert resource.is_file()
    assert "[[prompts]]" in resource.read_text(encoding="utf-8")
