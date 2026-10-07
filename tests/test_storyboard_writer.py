"""Tests for ``storyboard_writer@v1`` (GENERATION_ACCEPTANCE §11, SBW; ADR-0089).

Consistency, not wording: the Agent, Prompt and Schema agree, the Prompt states the limits the
decoder enforces, and the role compiles into an executor with the Schema binding.
"""

from __future__ import annotations

from omemo_content_factory.agents import storyboard_writer
from omemo_content_factory.application import storyboard
from omemo_content_factory.composition import build_storyboard_writing, load_prompt_catalogue
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient


def test_sbw_01_the_role_binds_its_prompt_and_schema() -> None:
    agent = storyboard_writer.STORYBOARD_WRITER_AGENT
    prompt = load_prompt_catalogue()[agent.prompt_ref]
    assert prompt.schema_ref == storyboard_writer.SCHEMA_REF
    assert storyboard_writer.STORYBOARD_SCHEMA.view.required_fields == storyboard.STORYBOARD_FIELDS
    assert "{input}" in prompt.user_template
    assert agent.skill_refs == () and agent.tool_refs == ()


def test_sbw_02_the_prompt_names_the_fields_the_grammar_and_the_limits() -> None:
    system = load_prompt_catalogue()[storyboard_writer.PROMPT_REF].system
    for field in storyboard.STORYBOARD_FIELDS:
        assert field in system
    assert "lines | cast | picture | motion" in system
    assert f"Не меньше {storyboard.MIN_SHOTS} планов" in system
    assert "не больше трёх реплик" in system and "ВСЕ реплики" in system
    assert "9:16" in system and "АНГЛИЙСКОМ" in system


def test_sbw_03_it_compiles_into_an_executor_and_a_binding() -> None:
    writing = build_storyboard_writing(FakeLLMClient())
    assert writing.schema_binding.schema_ref == storyboard_writer.SCHEMA_REF
    result = writing.executor.execute("Title: T")
    assert result.succeeded
    assert set(result.payload_fields or {}) == set(storyboard.STORYBOARD_FIELDS)
