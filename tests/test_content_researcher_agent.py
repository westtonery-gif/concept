"""Tests for the ``content_researcher@v1`` (Rin) role definition.

Verify the migrated assets are valid and self-consistent, that the Composition Root resolves them
without change (agent -> prompt -> schema -> executor with the right generation shape), and that a
keyless run through the assembled ContentDirector produces a schema-valid ``content-research``
Output. Same shape as the ``script_writer`` (Leo) tests — this is Pattern Application.
"""

from __future__ import annotations

from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.application.skill_execution import SkillPreprocessingTaskExecutor
from omemo_content_factory.composition import (
    build_content_director,
    build_executor_map,
    load_prompt_catalogue,
)
from omemo_content_factory.domain.run import Run, RunStatus
from omemo_content_factory.domain.schema import SchemaStatus
from omemo_content_factory.domain.task import TaskStatus
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient
from omemo_content_factory.infrastructure.llm import LLMTaskExecutor


def test_assets_are_valid_and_self_consistent() -> None:
    view = rin.CONTENT_RESEARCH_SCHEMA.view
    assert view.status is SchemaStatus.ACTIVE
    assert view.required_fields == ("audience", "angle")
    assert rin.CONTENT_RESEARCHER_AGENT.agent_id == "content_researcher@v1"
    assert rin.CONTENT_RESEARCHER_AGENT.skill_refs == ("normalize_terminology@v1",)
    # agent -> external Prompt -> schema references chain together through the Root.
    prompt = load_prompt_catalogue()[rin.CONTENT_RESEARCHER_AGENT.prompt_ref]
    assert rin.SCHEMAS[prompt.schema_ref] is rin.CONTENT_RESEARCH_SCHEMA
    assert prompt.schema_ref == "content-research-report"


def test_composition_resolves_rin_with_generation_shape_and_skill() -> None:
    executors = build_executor_map(
        rin.AGENTS,
        None,
        FakeLLMClient(),
        rin.SCHEMAS,
        skill_invocations=rin.SKILL_INVOCATIONS,
    )
    executor = executors[rin.AGENT_REF]
    assert isinstance(executor, SkillPreprocessingTaskExecutor)
    assert isinstance(executor.delegate, LLMTaskExecutor)
    assert executor.delegate.schema_ref == "content-research-report"
    assert executor.delegate.output_fields == ("audience", "angle")  # projected from Schema


def test_keyless_run_produces_valid_content_research() -> None:
    director = build_content_director(
        rin.AGENTS,
        None,
        FakeLLMClient(),
        rin.SCHEMAS,
        skill_invocations=rin.SKILL_INVOCATIONS,
    )
    run = Run.create(run_id="rin-1", content_brief_ref="brief", workflow_version_ref="wf@1")
    workflow = Workflow.create(
        workflow_id="wf",
        name="wf",
        steps=[
            WorkflowStep(
                step_id="s1",
                task_type="research",
                agent_ref=rin.AGENT_REF,
                schema_ref=rin.SCHEMA_REF,
            )
        ],
    )

    director.execute_workflow(run, workflow, brief="умная кофеварка")

    assert run.status is RunStatus.COMPLETED
    assert [v.status for v in run.tasks] == [TaskStatus.SUCCEEDED]
    assert run.task(run.tasks[0].task_id).output is not None  # Output recorded via validating path


def test_fake_output_satisfies_the_schema() -> None:
    # The keyless provider's output for the required fields is schema-valid (audience/angle).
    fields = rin.CONTENT_RESEARCH_SCHEMA.view.required_fields
    payload = FakeLLMClient().complete(system="s", user="brief", fields=fields)
    assert rin.CONTENT_RESEARCH_SCHEMA.validate(payload.fields).is_valid
