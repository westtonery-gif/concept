"""Tests for ``clip_post_writer@v1``, the clip's post text (ADR-0072).

Consistency, not wording: the Agent, Prompt and Schema agree, the role is granted nothing, and it
compiles into an executor with the Schema binding its Output is validated against.
"""

from __future__ import annotations

from omemo_content_factory.agents import clip_post_writer
from omemo_content_factory.composition import build_post_writing, load_prompt_catalogue
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient


def test_cpw_01_the_role_binds_its_prompt_and_schema() -> None:
    agent = clip_post_writer.CLIP_POST_WRITER_AGENT
    prompt = load_prompt_catalogue()[agent.prompt_ref]
    assert prompt.schema_ref == clip_post_writer.SCHEMA_REF
    assert clip_post_writer.CLIP_POST_SCHEMA.view.required_fields == ("title", "description")
    assert "{input}" in prompt.user_template
    assert agent.skill_refs == () and agent.tool_refs == ()


def test_cpw_02_the_prompt_asks_for_a_title_under_youtubes_limit_and_hashtags() -> None:
    system = load_prompt_catalogue()[clip_post_writer.PROMPT_REF].system
    assert "90 символов" in system, "under POST_TITLE_LIMIT (100) with room to spare"
    assert "хэштег" in system


def test_cpw_03_it_compiles_into_an_executor_and_a_binding() -> None:
    writing = build_post_writing(FakeLLMClient())
    assert writing.schema_binding.schema_ref == clip_post_writer.SCHEMA_REF
    result = writing.executor.execute('{"artifact_ref": "a", "transcript": "x"}')
    assert result.succeeded
    assert set(result.payload_fields or {}) == {"title", "description"}
