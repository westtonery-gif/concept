"""Tests for the clipping department's QA role, ``clip_qa_agent@v1`` (ADR-0056).

Maps CLIPPING_ACCEPTANCE.md §6 (CQA). These pin the role's **consistency** — that its Agent,
Prompt and Schema agree, that it reuses the one verdict contract and that it is granted nothing —
not the Prompt's wording, which is content reviewed like content (PROJECT.md §15).
"""

from __future__ import annotations

import pytest

from omemo_content_factory.agents import clip_qa_agent, qa_agent
from omemo_content_factory.application.qa_evaluation import QA_VERDICT_FIELDS
from omemo_content_factory.composition import (
    CompositionError,
    load_prompt_catalogue,
    validate_workflow_executors,
)
from omemo_content_factory.domain.schema import SchemaStatus
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep


def _prompt() -> object:
    return load_prompt_catalogue()[clip_qa_agent.PROMPT_REF]


def test_cqa_01_the_role_binds_its_prompt_and_schema() -> None:
    agent = clip_qa_agent.CLIP_QA_AGENT
    assert agent.agent_id == "clip_qa_agent@v1"
    assert agent.prompt_ref == "clip-qa-agent"
    prompt = load_prompt_catalogue()[agent.prompt_ref]
    assert prompt.schema_ref == clip_qa_agent.SCHEMA_REF
    assert (
        clip_qa_agent.SCHEMAS[clip_qa_agent.SCHEMA_REF]
        is clip_qa_agent.SCHEMAS[clip_qa_agent.SCHEMA_REF]
    )


def test_cqa_01_the_role_is_granted_no_skills_and_no_tools() -> None:
    """A QA role that could call something could steer the pipeline it is meant to judge."""
    agent = clip_qa_agent.CLIP_QA_AGENT
    assert agent.skill_refs == ()
    assert agent.tool_refs == ()


def test_cqa_02_the_verdict_contract_is_the_one_that_already_exists() -> None:
    """One verdict vocabulary, one decoder. A second would be a second thing to keep honest."""
    assert clip_qa_agent.SCHEMA_REF == qa_agent.SCHEMA_REF
    schema = clip_qa_agent.SCHEMAS[clip_qa_agent.SCHEMA_REF]
    assert schema is qa_agent.QA_VERDICT_SCHEMA, "the Schema object itself is reused, not rebuilt"
    view = schema.view
    assert view.required_fields == QA_VERDICT_FIELDS
    assert view.status is SchemaStatus.ACTIVE


def test_cqa_02_the_two_qa_roles_are_distinct_but_answer_the_same_shape() -> None:
    assert clip_qa_agent.AGENT_REF != qa_agent.AGENT_REF
    assert clip_qa_agent.PROMPT_REF != qa_agent.PROMPT_REF


def test_cqa_03_the_prompt_carries_exactly_the_three_criteria() -> None:
    system = load_prompt_catalogue()[clip_qa_agent.PROMPT_REF].system
    numbered = [
        line.strip() for line in system.splitlines() if line.strip()[:2] in {"1)", "2)", "3)", "4)"}
    ]
    assert len(numbered) == 3, (
        "ADR-0058 §3 withdrew the fourth criterion; a stale one must not linger"
    )
    assert numbered[0].startswith("1)")
    assert numbered[2].startswith("3)")


def test_cqa_03_the_prompt_tells_the_model_not_to_judge_the_format() -> None:
    """Format is arithmetic (ADR-0056 §1); a model spending flags on it would fill the queue."""
    system = load_prompt_catalogue()[clip_qa_agent.PROMPT_REF].system
    assert "Не проверяй" in system
    for word in ("длительность", "формат", "разрешение", "соотношение сторон"):
        assert word in system


def test_cqa_03_the_prompt_keeps_the_adr_0034_verdict_grammar() -> None:
    system = load_prompt_catalogue()[clip_qa_agent.PROMPT_REF].system
    for token in ("passed", "flagged", "failed"):
        assert token in system
    assert "JSON" in system
    assert all(field in system for field in QA_VERDICT_FIELDS)


def test_cqa_04_the_prompt_asks_for_the_whole_episode_next_to_the_clip() -> None:
    """Criterion 3 cannot be applied without it — the clip alone never reveals a later beat."""
    system = load_prompt_catalogue()[clip_qa_agent.PROMPT_REF].system
    assert "расшифровку всей серии" in system


def test_cqa_05_the_role_is_refused_as_a_workflow_step() -> None:
    """It answers with a verdict, not an Output: it belongs behind the evaluator port."""
    workflow = Workflow.create(
        workflow_id="clip-qa-as-a-step@v1",
        name="misuse",
        steps=(
            WorkflowStep(
                step_id="s1",
                task_type="clip_qa",
                agent_ref=clip_qa_agent.AGENT_REF,
                schema_ref=clip_qa_agent.SCHEMA_REF,
            ),
        ),
    )
    with pytest.raises(CompositionError):
        validate_workflow_executors(workflow, executors={})
