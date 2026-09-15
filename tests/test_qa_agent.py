"""QAR acceptance: the ``qa_agent@v1`` role definition (ADR-0035, EVALUATION_ACCEPTANCE §4.2).

The role's assets are valid and self-consistent, resolve through the Composition Root without a
model call, and its Prompt teaches exactly the verdict grammar the ADR-0034 decoder accepts.
"""

from __future__ import annotations

from omemo_content_factory.agents import qa_agent as qa
from omemo_content_factory.application.qa_evaluation import QA_VERDICT_FIELDS, decode_verdict
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.composition import build_schema_map, load_prompt_catalogue
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.prompt import PromptVersion
from omemo_content_factory.domain.schema import SchemaStatus


def test_qar_01_schema_is_active_and_declares_exactly_the_verdict_fields() -> None:
    view = qa.QA_VERDICT_SCHEMA.view
    assert view.status is SchemaStatus.ACTIVE
    assert view.required_fields == QA_VERDICT_FIELDS == ("verdict", "flags")
    assert qa.SCHEMAS == {"qa-verdict@v1": qa.QA_VERDICT_SCHEMA}


def test_qar_02_agent_chains_to_its_prompt_and_schema_with_no_skills_or_tools() -> None:
    assert qa.AGENTS == (qa.QA_AGENT,)
    assert qa.QA_AGENT.agent_id == "qa_agent@v1"
    assert qa.QA_AGENT.prompt_ref == "qa-agent"
    assert qa.QA_AGENT.skill_refs == ()
    assert qa.QA_AGENT.tool_refs == ()
    prompt = load_prompt_catalogue()[qa.QA_AGENT.prompt_ref]
    assert prompt.version == PromptVersion(1)
    assert prompt.schema_ref == qa.SCHEMA_REF
    assert qa.SCHEMAS[prompt.schema_ref] is qa.QA_VERDICT_SCHEMA


def test_qar_03_prompt_teaches_the_decoder_grammar() -> None:
    prompt = load_prompt_catalogue()[qa.PROMPT_REF]
    for token in (*QA_VERDICT_FIELDS, "passed", "flagged", "failed", "JSON", "[]"):
        assert token in prompt.system, token
    assert prompt.user_template.count("{input}") == 1
    for field in QA_VERDICT_FIELDS:
        assert field in prompt.user_template


def test_qar_04_composition_root_binds_the_role_to_its_schema() -> None:
    bindings = build_schema_map(qa.AGENTS, None, qa.SCHEMAS)
    assert bindings == {qa.AGENT_REF: SchemaBinding(qa.SCHEMA_REF, qa.QA_VERDICT_SCHEMA)}


def test_qar_05_schema_and_decoder_agree_on_a_verdict_without_flags() -> None:
    answer = {"verdict": "passed", "flags": "[]"}
    assert qa.QA_VERDICT_SCHEMA.validate(answer).is_valid
    assert decode_verdict(answer).verdict is EvaluationStatus.PASSED
