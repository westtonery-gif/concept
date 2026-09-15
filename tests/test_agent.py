"""Tests for the Agent and Prompt descriptors (encode AGENT_SPEC criteria; tests = acceptance).

Agent / Prompt are pure immutable descriptors: identity + references + immutable text. They carry no
execution, orchestration or decision logic — only the data contract is exercised here.
"""

from __future__ import annotations

import dataclasses

import pytest

from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.prompt import Prompt, PromptVersion


def _prompt() -> Prompt:
    return Prompt(
        prompt_id="research-notes",
        version=PromptVersion(1),
        system="You are a researcher.",
        user_template="Brief: {input}",
        schema_ref="research-notes@1",
    )


def _agent(prompt_ref: str = "research-notes") -> Agent:
    return Agent(agent_id="researcher@v1", name="Researcher", prompt_ref=prompt_ref)


# --- Agent: descriptor + binding ---------------------------------------------------------


def test_agent_links_agent_ref_to_prompt_ref() -> None:
    prompt = _prompt()
    agent = _agent(prompt_ref=prompt.prompt_id)
    assert agent.agent_id == "researcher@v1"
    assert agent.prompt_ref == prompt.prompt_id  # agent_ref -> prompt_id (1:1)
    assert agent.description == ""  # optional, non-execution metadata


def test_agt_t1_agent_is_granted_no_tools_by_default() -> None:
    # ADR-0022 §4: the grant fails closed — a role reaches no Tool unless it lists it.
    assert _agent().tool_refs == ()
    granted = Agent(agent_id="w@v1", name="W", prompt_ref="p", tool_refs=("text_metrics@v1",))
    assert granted.tool_refs == ("text_metrics@v1",)


def test_agent_is_immutable() -> None:
    agent = _agent()
    for field in ("agent_id", "name", "prompt_ref", "description", "tool_refs"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(agent, field, "x")


def test_agents_compare_by_value() -> None:
    assert _agent() == _agent()
    assert _agent() != _agent(prompt_ref="other")


# --- Prompt: immutable artifact ----------------------------------------------------------


def test_prompt_is_immutable() -> None:
    prompt = _prompt()
    for field in ("prompt_id", "version", "system", "user_template", "schema_ref"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(prompt, field, "x")


def test_prompt_carries_schema_ref_as_plain_reference() -> None:
    # schema_ref only references the authoritative Schema (ADR-0008), not owns it.
    assert _prompt().schema_ref == "research-notes@1"


def test_prompt_version_compared_by_value() -> None:
    assert PromptVersion(3) == PromptVersion(3)
    assert PromptVersion(3) != PromptVersion(4)


def test_new_prompt_revision_is_a_new_version() -> None:
    v1 = _prompt()
    v2 = Prompt(
        prompt_id="research-notes",
        version=PromptVersion(2),
        system="You are a careful researcher.",
        user_template="Brief: {input}",
        schema_ref="research-notes@2",
    )
    assert v1.prompt_id == v2.prompt_id
    assert v1.version != v2.version
    assert v1 == _prompt()  # the earlier value is unchanged
