"""Tests for ``story_writer@v1`` (GENERATION_ACCEPTANCE §9, STW; ADR-0087).

Consistency, not wording: the Agent, Prompt and Schema agree, the role is granted nothing, the
Prompt states the limits the decoder enforces, and it compiles into an executor with the Schema
binding its Output is validated against.
"""

from __future__ import annotations

from omemo_content_factory.agents import story_writer
from omemo_content_factory.application import story_script
from omemo_content_factory.composition import build_story_writing, load_prompt_catalogue
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient


def test_stw_01_the_role_binds_its_prompt_and_schema() -> None:
    agent = story_writer.STORY_WRITER_AGENT
    prompt = load_prompt_catalogue()[agent.prompt_ref]
    assert prompt.schema_ref == story_writer.SCHEMA_REF
    assert story_writer.STORY_SCRIPT_SCHEMA.view.required_fields == story_script.STORY_FIELDS
    assert "{input}" in prompt.user_template
    assert agent.skill_refs == () and agent.tool_refs == ()


def test_stw_02_the_prompt_names_every_field_and_the_limits_the_decoder_enforces() -> None:
    system = load_prompt_catalogue()[story_writer.PROMPT_REF].system
    for field in story_script.STORY_FIELDS:
        assert field in system
    assert str(story_script.MAX_TITLE_CHARS) in system
    assert str(story_script.MAX_LINE_WORDS) in system
    assert f"от {story_script.MIN_LINES} до {story_script.MAX_LINES} реплик" in system
    assert "key | реплика" in system and "key | look | voice" in system


def test_stw_03_the_prompt_carries_the_skeleton_and_the_safety_rules() -> None:
    system = load_prompt_catalogue()[story_writer.PROMPT_REF].system
    assert "первая реплика" in system.lower() and "эскалация" in system.lower()
    assert "АНГЛИЙСКОМ" in system
    assert "не ставь детей в опасность" in system.lower()
    assert "франшиз" in system.lower(), "original characters only"


def test_stw_04_it_compiles_into_an_executor_and_a_binding() -> None:
    writing = build_story_writing(FakeLLMClient())
    assert writing.schema_binding.schema_ref == story_writer.SCHEMA_REF
    result = writing.executor.execute("idea: a peach with a timer")
    assert result.succeeded
    assert set(result.payload_fields or {}) == set(story_script.STORY_FIELDS)
