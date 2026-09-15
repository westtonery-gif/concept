"""Milestone M2 acceptance — one real pass through the Content Director (ADR-0033, M2A).

The ``research-to-script@v1`` Workflow (Rin -> Leo) is compiled by the real Composition Root from
production assets only: the bundled versioned Prompt store, Rin's Skill invocation and Tool grant,
the real ``AnthropicLLMClient`` with explicit pricing, and the real ``SqliteRunStore``. The only
substitute is the network below the SDK: a scripted ``messages.create`` returning real SDK
``Message`` objects. M2A-09 uses Director-level fakes, as the rework acceptance does.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import anthropic
import pytest
from anthropic.types import ContentBlock, Message, ToolUseBlock, Usage

from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.content_director import (
    INVALID_OUTPUT_REASON,
    REWORK_NO_OUTPUT_REASON,
    ContentDirector,
    TaskRequest,
)
from omemo_content_factory.application.qa_evaluation import EvaluationResult
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.composition import (
    RUN_STORE_PATH_VAR,
    build_available_tools,
    build_run_store,
    compile_runtime,
    load_prompt_catalogue,
)
from omemo_content_factory.domain.artifact import ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.output import OutputStatus
from omemo_content_factory.domain.run import Actor, ReworkPolicy, Run, RunStatus
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.task import TaskStatus
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.llm import AnthropicLLMClient, TokenPricing

RUN_ID = "run-m2-acceptance"
BRIEF = "Тема: магний и сон для подписчиков омемо. Цель: короткий вертикальный сценарий."
_T0 = datetime(2026, 9, 15, 9, tzinfo=UTC)
_TODAY = datetime(2026, 9, 15, 18, 30, tzinfo=UTC)
_PRICING = TokenPricing(Decimal("3"), Decimal("15"), "USD")

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


# --- the scripted transport below the Anthropic SDK ---------------------------------------


class _ScriptedMessages:
    """``messages.create`` returning scripted Messages and keeping a snapshot of every request."""

    def __init__(self, responses: list[Message]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> Message:
        self.calls.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("unexpected provider turn")
        return self.responses.pop(0)


class _ScriptedAnthropic:
    def __init__(self, responses: list[Message]) -> None:
        self.messages = _ScriptedMessages(responses)


def _call(call_id: str, name: str, arguments: dict[str, object]) -> ToolUseBlock:
    return ToolUseBlock(id=call_id, input=arguments, name=name, type="tool_use")


def _message(*blocks: ContentBlock, model: str, input_tokens: int, output_tokens: int) -> Message:
    return Message(
        id="msg_m2",
        content=list(blocks),
        model=model,
        role="assistant",
        stop_reason="tool_use",
        stop_sequence=None,
        type="message",
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _date_turn() -> Message:
    return _message(
        _call("date-1", "current_date", {}),
        model="claude-rin-actual",
        input_tokens=1200,
        output_tokens=40,
    )


def _happy_turns() -> list[Message]:
    """Rin asks for the date, then answers; Leo answers in one forced turn."""
    return [
        _date_turn(),
        _message(
            _call("final-1", "emit_fields", {"audience": "взрослые 30+", "angle": "сон"}),
            model="claude-rin-actual",
            input_tokens=1400,
            output_tokens=90,
        ),
        _message(
            _call(
                "final-2",
                "emit_fields",
                {"title": "Магний и сон", "hook": "Не высыпаетесь?", "script": "Сцена 1."},
            ),
            model="claude-leo-actual",
            input_tokens=900,
            output_tokens=300,
        ),
    ]


def _invalid_turns() -> list[Message]:
    """Rin asks for the date, then answers without the required ``angle``."""
    return [
        _date_turn(),
        _message(
            _call("final-1", "emit_fields", {"audience": "взрослые 30+"}),
            model="claude-rin-actual",
            input_tokens=1400,
            output_tokens=90,
        ),
    ]


def _ticking_clock() -> Callable[[], datetime]:
    ticks = iter(range(1_000))
    return lambda: _T0 + timedelta(milliseconds=100 * next(ticks))


def _user_text(call: dict[str, object]) -> str:
    messages = cast(list[dict[str, object]], call["messages"])
    return cast(str, messages[0]["content"])


def _tool_names(call: dict[str, object]) -> list[object]:
    return [tool["name"] for tool in cast(list[dict[str, object]], call["tools"])]


def _tool_result(call: dict[str, object]) -> tuple[dict[str, object], bool]:
    messages = cast(list[dict[str, object]], call["messages"])
    content = cast(list[dict[str, object]], messages[-1]["content"])
    return json.loads(cast(str, content[0]["content"])), cast(bool, content[0]["is_error"])


# --- the pass --------------------------------------------------------------------------


def _environ(tmp_path: Path) -> dict[str, str]:
    return {RUN_STORE_PATH_VAR: str(tmp_path / "m2" / "runs.sqlite3")}


def _compile(turns: list[Message], store: RunStore) -> tuple[ContentDirector, _ScriptedMessages]:
    scripted = _ScriptedAnthropic(turns)
    client = AnthropicLLMClient(
        model="configured-alias",
        pricing=_PRICING,
        client=cast(anthropic.Anthropic, scripted),
        clock=_ticking_clock(),
    )
    director = compile_runtime(
        AGENTS,
        None,
        client,
        WORKFLOW,
        SCHEMAS,
        skill_invocations=SKILL_INVOCATIONS,
        available_tools=build_available_tools(clock=lambda: _TODAY),
        store=store,
    )
    return director, scripted.messages


def _new_run() -> Run:
    return Run.create(
        run_id=RUN_ID,
        content_brief_ref="brief-magnesium-sleep",
        workflow_version_ref=WORKFLOW.workflow_id,
    )


def _m2_pass(tmp_path: Path, turns: list[Message]) -> tuple[Run, _ScriptedMessages, dict[str, str]]:
    environ = _environ(tmp_path)
    director, endpoint = _compile(turns, build_run_store(environ))
    run = _new_run()
    director.execute_workflow(run, WORKFLOW, brief=BRIEF)
    return run, endpoint, environ


def _prompt_ref(prompt_id: str) -> str:
    prompt = load_prompt_catalogue()[prompt_id]
    return f"{prompt.prompt_id}@v{prompt.version.value}"


# --- M2A -------------------------------------------------------------------------------


def test_m2a_01_the_workflow_completes_with_valid_outputs_and_provenance(tmp_path: Path) -> None:
    run, _, _ = _m2_pass(tmp_path, _happy_turns())

    assert run.status is RunStatus.COMPLETED
    research, script = run.tasks
    assert [research.status, script.status] == [TaskStatus.SUCCEEDED, TaskStatus.SUCCEEDED]
    assert research.output is not None
    assert script.output is not None
    assert [(o.status, o.schema_ref) for o in (research.output, script.output)] == [
        (OutputStatus.VALID, rin.SCHEMA_REF),
        (OutputStatus.VALID, leo.SCHEMA_REF),
    ]
    assert script.task_input == research.output.payload
    assert [artifact.output_ref for artifact in run.artifacts] == [
        research.output.output_id,
        script.output.output_id,
    ]


def test_m2a_02_provider_receives_the_externally_stored_prompt_version(tmp_path: Path) -> None:
    run, endpoint, _ = _m2_pass(tmp_path, _happy_turns())

    catalogue = load_prompt_catalogue()
    rin_prompt, leo_prompt = catalogue[rin.PROMPT_REF], catalogue[leo.PROMPT_REF]
    first, second, leo_turn = endpoint.calls
    assert [call["system"] for call in endpoint.calls] == [
        rin_prompt.system,
        rin_prompt.system,
        leo_prompt.system,
    ]
    research_output = run.tasks[0].output
    assert research_output is not None
    before, after = leo_prompt.user_template.split("{input}")
    leo_user = _user_text(leo_turn)
    assert leo_user.startswith(before)
    assert leo_user.endswith(after)
    assert research_output.payload in leo_user
    assert _user_text(first).startswith(rin_prompt.user_template.split("{input}")[0])
    assert _user_text(second) == _user_text(first)
    assert [record.prompt_ref for record in run.analytics_records] == [
        _prompt_ref(rin.PROMPT_REF),
        _prompt_ref(rin.PROMPT_REF),
        _prompt_ref(leo.PROMPT_REF),
    ]


def test_m2a_03_the_skill_normalizes_what_the_model_sees_not_the_stored_input(
    tmp_path: Path,
) -> None:
    run, endpoint, _ = _m2_pass(tmp_path, _happy_turns())

    rin_user = _user_text(endpoint.calls[0])
    assert run.tasks[0].task_input == BRIEF
    assert "омемо" in BRIEF
    assert "OMEMO" in rin_user
    assert "омемо" not in rin_user.casefold()


def test_m2a_04_the_tool_runs_mid_reasoning_only_for_the_granted_role(tmp_path: Path) -> None:
    _, endpoint, _ = _m2_pass(tmp_path, _happy_turns())

    first, second, leo_turn = endpoint.calls
    assert _tool_names(first) == ["current_date", "emit_fields"]
    assert _tool_names(second) == ["current_date", "emit_fields"]
    payload, is_error = _tool_result(second)
    assert is_error is False
    assert payload["status"] == "ok"
    assert cast(dict[str, object], payload["data"])["date"] == "2026-09-15"
    assert _tool_names(leo_turn) == ["emit_fields"]
    assert leo_turn["tool_choice"] == {"type": "tool", "name": "emit_fields"}


def test_m2a_05_every_provider_turn_is_one_analytics_record(tmp_path: Path) -> None:
    run, _, _ = _m2_pass(tmp_path, _happy_turns())

    records = run.analytics_records
    rin_task, leo_task = (task.task_id for task in run.tasks)
    assert [(r.run_id, r.task_id, r.agent_ref) for r in records] == [
        (RUN_ID, rin_task, rin.AGENT_REF),
        (RUN_ID, rin_task, rin.AGENT_REF),
        (RUN_ID, leo_task, leo.AGENT_REF),
    ]
    assert [(r.provider, r.model) for r in records] == [
        ("anthropic", "claude-rin-actual"),
        ("anthropic", "claude-rin-actual"),
        ("anthropic", "claude-leo-actual"),
    ]
    assert [(r.token_usage.input_tokens, r.token_usage.output_tokens) for r in records] == [
        (1200, 40),
        (1400, 90),
        (900, 300),
    ]
    assert [(r.cost.amount, r.cost.currency) for r in records] == [
        (Decimal("0.0042"), "USD"),
        (Decimal("0.00555"), "USD"),
        (Decimal("0.0072"), "USD"),
    ]
    ms = timedelta(milliseconds=100)
    assert [(r.time_range.started_at, r.time_range.finished_at) for r in records] == [
        (_T0, _T0 + ms),
        (_T0 + 2 * ms, _T0 + 3 * ms),
        (_T0 + 4 * ms, _T0 + 5 * ms),
    ]
    assert [r.retries for r in records] == [0, 0, 0]


def test_m2a_06_the_sqlite_row_is_the_run_and_resume_calls_no_model(tmp_path: Path) -> None:
    run, _, environ = _m2_pass(tmp_path, _happy_turns())

    assert Path(environ[RUN_STORE_PATH_VAR]).is_file()
    back = build_run_store(environ).load(RUN_ID)
    assert back is not None
    assert back.snapshot == run.snapshot

    director, endpoint = _compile([], build_run_store(environ))
    director.resume_workflow(back, WORKFLOW, brief=BRIEF)

    assert endpoint.calls == []
    assert back.snapshot == run.snapshot


def test_m2a_07_an_invalid_answer_is_a_contract_error_not_content(tmp_path: Path) -> None:
    run, endpoint, environ = _m2_pass(tmp_path, _invalid_turns())

    assert run.status is RunStatus.FAILED
    assert run.failure_reason == INVALID_OUTPUT_REASON
    (research,) = run.tasks
    assert research.status is TaskStatus.SUCCEEDED
    assert research.output is not None
    assert research.output.status is OutputStatus.INVALID
    assert len(run.artifacts) == 0
    assert len(endpoint.calls) == 2
    assert len(run.analytics_records) == 2

    back = build_run_store(environ).load(RUN_ID)
    assert back is not None
    assert back.snapshot == run.snapshot
    director, again = _compile([], build_run_store(environ))
    director.resume_workflow(back, WORKFLOW, brief=BRIEF)
    assert again.calls == []
    assert back.status is RunStatus.FAILED


class _ProcessCrashError(Exception):
    """The process stops right after a selected commit."""


class _CrashAfterInvalidOutput:
    """Commit, then die once the committed Run holds an INVALID Output but is still running."""

    def __init__(self, inner: RunStore) -> None:
        self._inner = inner

    def save(self, run: Run, /) -> None:
        self._inner.save(run)
        invalid = any(
            task.output is not None and task.output.status is OutputStatus.INVALID
            for task in run.tasks
        )
        if invalid and run.status is RunStatus.RUNNING:
            raise _ProcessCrashError

    def load(self, run_id: str, /) -> Run | None:
        return self._inner.load(run_id)


def _restored_after_invalid_output(store: RunStore, environ: dict[str, str]) -> Run:
    """Crash right after the INVALID Output is committed; return the Run as a restart sees it."""
    director, _ = _compile(_invalid_turns(), _CrashAfterInvalidOutput(build_run_store(environ)))
    with pytest.raises(_ProcessCrashError):
        director.execute_workflow(_new_run(), WORKFLOW, brief=BRIEF)
    back = store.load(RUN_ID)
    assert back is not None
    assert back.status is RunStatus.RUNNING
    return back


def test_m2a_08_a_run_restored_before_its_failure_is_failed_without_a_call(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    store = build_run_store(environ)
    back = _restored_after_invalid_output(store, environ)
    restarted, endpoint = _compile([], store)
    restarted.resume_workflow(back, WORKFLOW, brief=BRIEF)

    assert endpoint.calls == []
    assert back.status is RunStatus.FAILED
    assert back.failure_reason == INVALID_OUTPUT_REASON
    assert len(back.tasks) == 1
    assert len(back.artifacts) == 0
    stored = store.load(RUN_ID)
    assert stored is not None
    assert stored.status is RunStatus.FAILED


# --- M2A-09: the rework route, with Director-level fakes ---------------------------------

_REQUESTS = [
    TaskRequest("research", "researcher@v1", "brief", artifact_kind="research"),
    TaskRequest("write", "writer@v1", "", artifact_kind="script"),
]


@dataclass
class _ReworkExecutor:
    """Valid originals; every rework call answers without the required ``text``."""

    calls: list[str] = field(default_factory=list)

    def execute(self, task_input: str) -> ExecutionResult:
        self.calls.append(task_input)
        if task_input in ("brief", "research"):
            text = "research" if task_input == "brief" else "script v1"
            return ExecutionResult(
                succeeded=True, output=text, schema_ref="s@v1", payload_fields={"text": text}
            )
        return ExecutionResult(succeeded=True, output="{}", schema_ref="s@v1", payload_fields={})


class _Flagging:
    def evaluate(self, content: str) -> EvaluationResult:
        return EvaluationResult(EvaluationStatus.FLAGGED, (f"risk in {content}",))


def _escalated_with_changes_requested(executor: _ReworkExecutor) -> tuple[ContentDirector, Run]:
    """Run v1 to a QA escalation and record the reviewer's ``CHANGES_REQUESTED``."""
    schema = Schema.create(
        schema_id="s", version=SchemaVersion(1), description="d", required_fields=["text"]
    )
    schema.transition(SchemaStatus.ACTIVE)
    binding = SchemaBinding("s@v1", schema)
    director = ContentDirector(
        executor, {"researcher@v1": binding, "writer@v1": binding}, qa=_Flagging()
    )
    run = Run.create(
        run_id="run-m2a-09",
        content_brief_ref="brief",
        workflow_version_ref="wf@v1",
        rework_policy=ReworkPolicy(max_rework_iterations=1),
    )
    director.execute(run, _REQUESTS)
    assert run.status is RunStatus.WAITING_HUMAN
    run.submit_review(
        run.human_reviews[-1].review_id,
        ReviewStatus.CHANGES_REQUESTED,
        by=Actor.HUMAN_REVIEWER,
        reason="Add a source.",
    )
    return director, run


def test_m2a_09_an_invalid_rework_output_never_becomes_a_version() -> None:
    executor = _ReworkExecutor()
    director, run = _escalated_with_changes_requested(executor)
    candidate = run.artifacts[-1]

    director.resume(run, _REQUESTS)

    assert run.status is RunStatus.FAILED
    assert run.failure_reason == REWORK_NO_OUTPUT_REASON
    rework_output = run.tasks[-1].output
    assert rework_output is not None
    assert rework_output.status is OutputStatus.INVALID
    assert len(run.artifacts) == 2
    assert run.artifact(candidate.artifact_id).status is ArtifactStatus.CANDIDATE
    assert len(executor.calls) == 3
