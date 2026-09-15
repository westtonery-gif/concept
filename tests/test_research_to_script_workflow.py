"""Integration slice: Rin (research) -> Leo (script) chained inside one real Workflow.

Each role is already proven **alone** (``test_content_researcher_agent.py``,
``test_script_writer_agent.py``): the Composition Root resolves it and a keyless run through the
assembled ``ContentDirector`` produces a schema-valid Output. This closes the remaining gap —
that the two migrated roles (ADR-0016) also work **together**, inside one multi-step Workflow
(ADR-0009 §8): the Composition Root can merge both catalogues into a single executor/schema map,
and the existing chaining (a Task's Output becomes the next Task's input; ``ContentDirector``) feeds
Rin's ``content-research`` Output into Leo's script-writing Task. No new domain concept — pure
Pattern Application over already-accepted ADR-0009/0011/0012/0013/0014/0016.
"""

from __future__ import annotations

from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.composition import compile_runtime
from omemo_content_factory.domain.output import OutputStatus
from omemo_content_factory.domain.run import Run, RunStatus
from omemo_content_factory.domain.task import TaskStatus
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.fake_llm import FakeLLMClient

AGENTS = (*rin.AGENTS, *leo.AGENTS)
SCHEMAS = {**rin.SCHEMAS, **leo.SCHEMAS}
SKILL_INVOCATIONS = {**rin.SKILL_INVOCATIONS}

WORKFLOW = Workflow.create(
    workflow_id="research-to-script@v1",
    name="Research -> Script",
    steps=[
        WorkflowStep(
            step_id="research",
            task_type="research",
            agent_ref=rin.AGENT_REF,
            schema_ref=rin.SCHEMA_REF,
        ),
        WorkflowStep(
            step_id="write_script",
            task_type="write_script",
            agent_ref=leo.AGENT_REF,
            schema_ref=leo.SCHEMA_REF,
        ),
    ],
)


def test_compile_runtime_resolves_both_migrated_roles() -> None:
    director = compile_runtime(
        AGENTS,
        None,
        FakeLLMClient(),
        WORKFLOW,
        SCHEMAS,
        skill_invocations=SKILL_INVOCATIONS,
    )
    run = Run.create(run_id="rin-leo-1", content_brief_ref="brief", workflow_version_ref="wf@1")

    director.execute_workflow(run, WORKFLOW, brief="умная кофеварка")

    assert run.status is RunStatus.COMPLETED
    assert [v.status for v in run.tasks] == [TaskStatus.SUCCEEDED, TaskStatus.SUCCEEDED]


def test_rins_output_becomes_leos_input() -> None:
    director = compile_runtime(
        AGENTS,
        None,
        FakeLLMClient(),
        WORKFLOW,
        SCHEMAS,
        skill_invocations=SKILL_INVOCATIONS,
    )
    run = Run.create(run_id="rin-leo-2", content_brief_ref="brief", workflow_version_ref="wf@1")

    director.execute_workflow(run, WORKFLOW, brief="умная кофеварка")

    research_output = run.tasks[0].output
    script_task = run.task(run.tasks[1].task_id)
    assert research_output is not None
    assert script_task.task_input == research_output.payload


def test_both_outputs_were_recorded_as_schema_valid() -> None:
    director = compile_runtime(
        AGENTS,
        None,
        FakeLLMClient(),
        WORKFLOW,
        SCHEMAS,
        skill_invocations=SKILL_INVOCATIONS,
    )
    run = Run.create(run_id="rin-leo-3", content_brief_ref="brief", workflow_version_ref="wf@1")

    director.execute_workflow(run, WORKFLOW, brief="умная кофеварка")

    research_output, script_output = (view.output for view in run.tasks)
    assert research_output is not None
    assert script_output is not None
    # Recorded through the unified validated path (schema_validation.py) — VALID, not reconstructed
    # by re-validating the serialized payload, which is one-directional (ADR-0014 §7).
    assert research_output.status is OutputStatus.VALID
    assert script_output.status is OutputStatus.VALID
    assert research_output.schema_ref == rin.SCHEMA_REF
    assert script_output.schema_ref == leo.SCHEMA_REF
