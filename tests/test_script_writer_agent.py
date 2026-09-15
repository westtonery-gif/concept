"""Tests for the ``script_writer@v1`` (Leo) role definition.

Verify the migrated assets are valid and self-consistent, that the Composition Root resolves them
without change (agent -> prompt -> schema -> executor with the right generation shape), and that a
keyless run through the assembled ContentDirector produces a schema-valid ``script-draft`` Output.
"""

from __future__ import annotations

from omemo_content_factory.agents import script_writer as leo
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
    view = leo.SCRIPT_DRAFT_SCHEMA.view
    assert view.status is SchemaStatus.ACTIVE
    assert view.required_fields == ("title", "hook", "script")
    assert leo.SCRIPT_WRITER_AGENT.agent_id == "script_writer@v1"
    # agent -> external Prompt -> schema references chain together through the Root.
    prompt = load_prompt_catalogue()[leo.SCRIPT_WRITER_AGENT.prompt_ref]
    assert leo.SCHEMAS[prompt.schema_ref] is leo.SCRIPT_DRAFT_SCHEMA
    assert prompt.schema_ref == "script-draft@v1"


def test_composition_resolves_leo_with_generation_shape() -> None:
    executors = build_executor_map(leo.AGENTS, None, FakeLLMClient(), leo.SCHEMAS)
    executor = executors[leo.AGENT_REF]
    assert isinstance(executor, LLMTaskExecutor)
    assert executor.schema_ref == "script-draft@v1"
    assert executor.output_fields == ("title", "hook", "script")  # shape projected from Schema


def test_keyless_run_produces_valid_script_draft() -> None:
    director = build_content_director(leo.AGENTS, None, FakeLLMClient(), leo.SCHEMAS)
    run = Run.create(run_id="leo-1", content_brief_ref="brief", workflow_version_ref="wf@1")
    workflow = Workflow.create(
        workflow_id="wf",
        name="wf",
        steps=[
            WorkflowStep(
                step_id="s1",
                task_type="write_script",
                agent_ref=leo.AGENT_REF,
                schema_ref=leo.SCHEMA_REF,
            )
        ],
    )

    director.execute_workflow(run, workflow, brief="умная кофеварка")

    assert run.status is RunStatus.COMPLETED
    assert [v.status for v in run.tasks] == [TaskStatus.SUCCEEDED]
    assert run.task(run.tasks[0].task_id).output is not None  # Output recorded via validating path


def test_fake_output_satisfies_the_schema() -> None:
    # The keyless provider's output for the required fields is schema-valid (title/hook/script).
    fields = leo.SCRIPT_DRAFT_SCHEMA.view.required_fields
    payload = FakeLLMClient().complete(system="s", user="brief", fields=fields)
    assert leo.SCRIPT_DRAFT_SCHEMA.validate(payload.fields).is_valid
