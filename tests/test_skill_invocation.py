"""Acceptance tests for the first Agent -> Skill consumer (`ADR-0027`, SCI)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.application.content_director import TaskRequest
from omemo_content_factory.application.skill_execution import SkillPreprocessingTaskExecutor
from omemo_content_factory.application.task_execution import ExecutionResult, TaskExecutor
from omemo_content_factory.composition import (
    CompositionError,
    build_content_director,
    build_executor_map,
)
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.run import Actor, Run, RunStatus
from omemo_content_factory.domain.task import TaskStatus


@dataclass
class RecordingExecutor:
    """Return one result and retain exactly what the Skill chain handed to the delegate."""

    result: ExecutionResult
    calls: list[str] = field(default_factory=list)

    def execute(self, task_input: str) -> ExecutionResult:
        self.calls.append(task_input)
        return self.result


@dataclass
class AppendingInvocation:
    """Small structural invocation used to make ordering and call counts observable."""

    skill_ref: str
    suffix: str
    calls: int = 0

    def apply_to(self, task_input: str, /) -> str:
        self.calls += 1
        return task_input + self.suffix


@dataclass
class RecordingClient:
    """Structured fake LLM client retaining the rendered user message."""

    calls: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)

    def complete(self, *, system: str, user: str, fields: Sequence[str]) -> dict[str, str]:
        self.calls.append((system, user, tuple(fields)))
        return {name: "ok" for name in fields}


def test_sci_03_invocations_run_in_order_and_delegate_result_is_unchanged() -> None:
    result = ExecutionResult(succeeded=False, failure_reason="delegate result")
    delegate = RecordingExecutor(result)
    first = AppendingInvocation("first@v1", "-first")
    second = AppendingInvocation("second@v1", "-second")
    executor: TaskExecutor = SkillPreprocessingTaskExecutor(delegate, (first, second))

    returned = executor.execute("input")

    assert first.calls == second.calls == 1
    assert delegate.calls == ["input-first-second"]
    assert returned is result


@pytest.mark.parametrize(
    "declared,configured",
    [
        (("first@v1",), ()),
        ((), (AppendingInvocation("first@v1", "-x"),)),
        (
            ("first@v1", "second@v1"),
            (AppendingInvocation("second@v1", "-2"), AppendingInvocation("first@v1", "-1")),
        ),
    ],
)
def test_sci_02_composition_refuses_skill_declaration_binding_mismatch(
    declared: tuple[str, ...], configured: tuple[AppendingInvocation, ...]
) -> None:
    client = RecordingClient()
    agent = Agent(
        agent_id="a@v1",
        name="A",
        prompt_ref=rin.PROMPT_REF,
        skill_refs=declared,
    )
    bindings = {agent.agent_id: configured} if configured else None

    with pytest.raises(CompositionError, match="declares skill_refs"):
        build_executor_map(
            [agent],
            rin.PROMPTS,
            client,
            rin.SCHEMAS,
            skill_invocations=bindings,
        )
    assert client.calls == []


def test_sci_04_rin_normalizes_model_input_but_task_keeps_original() -> None:
    client = RecordingClient()
    director = build_content_director(
        rin.AGENTS,
        rin.PROMPTS,
        client,
        rin.SCHEMAS,
        skill_invocations=rin.SKILL_INVOCATIONS,
    )
    original = "Бриф для омемо: объяснить пользу сна."
    run = Run.create(run_id="skill-rin-1", content_brief_ref="brief", workflow_version_ref="wf@1")

    director.execute(
        run,
        [
            TaskRequest(
                workflow_step_ref="research",
                agent_ref=rin.AGENT_REF,
                task_input=original,
            )
        ],
    )

    assert len(client.calls) == 1
    assert "OMEMO" in client.calls[0][1]
    assert "омемо" not in client.calls[0][1]
    assert run.tasks[0].task_input == original


def test_sci_05_resume_reapplies_skill_to_the_stored_original_input() -> None:
    client = RecordingClient()
    director = build_content_director(
        rin.AGENTS,
        rin.PROMPTS,
        client,
        rin.SCHEMAS,
        skill_invocations=rin.SKILL_INVOCATIONS,
    )
    original = "омемо исследует восстановление"
    request = TaskRequest(
        workflow_step_ref="research",
        agent_ref=rin.AGENT_REF,
        task_input=original,
    )
    run = Run.create(
        run_id="skill-rin-resume", content_brief_ref="brief", workflow_version_ref="wf@1"
    )
    run.transition(RunStatus.QUEUED, by=Actor.CONTENT_DIRECTOR)
    run.transition(RunStatus.RUNNING, by=Actor.CONTENT_DIRECTOR)
    task_id = run.open_task(
        workflow_step_ref=request.workflow_step_ref,
        agent_ref=request.agent_ref,
        task_input=request.task_input,
        by=Actor.CONTENT_DIRECTOR,
    )
    run.transition_task(task_id, TaskStatus.RUNNING, by=Actor.CONTENT_DIRECTOR)

    director.resume(run, [request])

    assert len(client.calls) == 1
    assert "OMEMO" in client.calls[0][1]
    assert run.task(task_id).task_input == original
    assert run.task(task_id).attempt_count == 2


def test_sci_06_agent_without_skills_keeps_bare_executor() -> None:
    agent = Agent(agent_id="a@v1", name="A", prompt_ref=rin.PROMPT_REF)
    executors = build_executor_map([agent], rin.PROMPTS, RecordingClient(), rin.SCHEMAS)
    assert not isinstance(executors[agent.agent_id], SkillPreprocessingTaskExecutor)
