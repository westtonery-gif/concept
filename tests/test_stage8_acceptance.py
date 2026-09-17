"""ROADMAP Stage 8 acceptance — the QA gate on a real pass through the Content Director (S8A).

``research-to-script@v1`` (Rin -> Leo) runs with the ``qa_agent@v1`` gate, compiled by the real
Composition Root from production assets only: the bundled Prompt store for all three roles, Rin's
Skill and Tool, a real ``AnthropicLLMClient`` for the producers and another, priced separately, for
the QA role, and the real ``SqliteRunStore``. The only substitute is the network below the SDK: a
scripted ``messages.create`` returning real SDK ``Message`` objects. Every restart compiles a new
Root over the same SQLite file (``STAGE8_ACCEPTANCE.md``, ADR-0039).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import anthropic
import pytest
from anthropic.types import ContentBlock, Message, ToolUseBlock, Usage

from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import qa_agent
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import QA_VERDICT_FIELDS, QaVerdictError
from omemo_content_factory.composition import (
    RUN_STORE_PATH_VAR,
    build_available_tools,
    build_qa_evaluator,
    build_run_store,
    compile_runtime,
    load_prompt_catalogue,
)
from omemo_content_factory.domain.artifact import ArtifactQaNotPassedError, ArtifactStatus
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunStatus
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.llm import AnthropicLLMClient, TokenPricing

RUN_ID = "run-stage8-acceptance"
BRIEF = "Тема: как выбрать беговые кроссовки. Цель: короткий вертикальный сценарий."
_T0 = datetime(2026, 9, 17, 9, tzinfo=UTC)
_TODAY = datetime(2026, 9, 17, 18, 30, tzinfo=UTC)
_PRODUCER_PRICING = TokenPricing(Decimal("3"), Decimal("15"), "USD")
_QA_PRICING = TokenPricing(Decimal("1"), Decimal("5"), "USD")
_CD = Actor.CONTENT_DIRECTOR
_REVIEWER = Actor.HUMAN_REVIEWER

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

FLAG = "цифра «70% бегунов» без источника"
INSTRUCTIONS = "Убери цифру или дай источник."


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


def _message(
    name: str, arguments: dict[str, object], *, model: str, tokens: tuple[int, int]
) -> Message:
    block: ContentBlock = ToolUseBlock(
        id=f"call-{name}", input=arguments, name=name, type="tool_use"
    )
    return Message(
        id="msg_s8",
        content=[block],
        model=model,
        role="assistant",
        stop_reason="tool_use",
        stop_sequence=None,
        type="message",
        usage=Usage(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


def _producer_turns() -> list[Message]:
    """Rin asks for the date, then answers; Leo answers in one forced turn."""
    return [
        _message("current_date", {}, model="claude-rin-actual", tokens=(1200, 40)),
        _message(
            "emit_fields",
            {"audience": "начинающие бегуны", "angle": "подбор по стопе"},
            model="claude-rin-actual",
            tokens=(1400, 90),
        ),
        _script_turn("Сцена 1. 70% бегунов покупают не тот размер."),
    ]


def _script_turn(script: str) -> Message:
    return _message(
        "emit_fields",
        {"title": "Кроссовки", "hook": "Болят колени?", "script": script},
        model="claude-leo-actual",
        tokens=(900, 300),
    )


def _verdict(verdict: str, flags: list[str]) -> Message:
    return _message(
        "emit_fields",
        {"verdict": verdict, "flags": json.dumps(flags, ensure_ascii=False)},
        model="claude-qa-actual",
        tokens=(2000, 60),
    )


def _ticking_clock() -> Callable[[], datetime]:
    ticks = iter(range(1_000))
    return lambda: _T0 + timedelta(milliseconds=100 * next(ticks))


def _user_text(call: dict[str, object]) -> str:
    messages = cast(list[dict[str, object]], call["messages"])
    return cast(str, messages[0]["content"])


# --- one process: a Root compiled over the SQLite file ------------------------------------


class _Process:
    """One process: the compiled Director, its two scripted endpoints and its store."""

    def __init__(self, environ: dict[str, str], producer: list[Message], qa: list[Message]) -> None:
        producer_sdk = _ScriptedAnthropic(producer)
        qa_sdk = _ScriptedAnthropic(qa)
        self.producer = producer_sdk.messages
        self.qa = qa_sdk.messages
        self.store = build_run_store(environ)
        producer_client = AnthropicLLMClient(
            model="producer-alias",
            pricing=_PRODUCER_PRICING,
            client=cast(anthropic.Anthropic, producer_sdk),
            clock=_ticking_clock(),
        )
        qa_client = AnthropicLLMClient(
            model="qa-alias",
            pricing=_QA_PRICING,
            client=cast(anthropic.Anthropic, qa_sdk),
            clock=_ticking_clock(),
        )
        self.director: ContentDirector = compile_runtime(
            AGENTS,
            None,
            producer_client,
            WORKFLOW,
            SCHEMAS,
            skill_invocations=SKILL_INVOCATIONS,
            available_tools=build_available_tools(clock=lambda: _TODAY),
            store=self.store,
            qa=build_qa_evaluator(qa_agent.QA_AGENT, None, qa_client, qa_agent.SCHEMAS),
        )

    def execute(self) -> Run:
        run = Run.create(
            run_id=RUN_ID,
            content_brief_ref="brief-running-shoes",
            workflow_version_ref=WORKFLOW.workflow_id,
        )
        self.director.execute_workflow(run, WORKFLOW, brief=BRIEF)
        return run

    def resume(self) -> Run:
        run = self.stored()
        self.director.resume_workflow(run, WORKFLOW, brief=BRIEF)
        return run

    def stored(self) -> Run:
        run = self.store.load(RUN_ID)
        assert run is not None
        return run


def _environ(tmp_path: Path) -> dict[str, str]:
    return {RUN_STORE_PATH_VAR: str(tmp_path / "stage8" / "runs.sqlite3")}


def _qa_prompt_ref() -> str:
    prompt = load_prompt_catalogue()[qa_agent.PROMPT_REF]
    return f"{prompt.prompt_id}@v{prompt.version.value}"


def _decide(environ: dict[str, str], decision: ReviewStatus, reason: str | None = None) -> Run:
    """The human reviewer decides the stored Run's pending Review; the decision is committed."""
    store = build_run_store(environ)
    run = store.load(RUN_ID)
    assert run is not None
    pending = [r for r in run.human_reviews if r.status is ReviewStatus.PENDING]
    assert len(pending) == 1
    run.submit_review(pending[0].review_id, decision, by=_REVIEWER, reason=reason)
    store.save(run)
    return run


# --- S8A ------------------------------------------------------------------------------


def test_s8a_01_a_passed_verdict_waits_for_the_human_and_an_approve_completes_the_run(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    process = _Process(environ, _producer_turns(), [_verdict("passed", [])])
    run = process.execute()

    assert run.status is RunStatus.WAITING_HUMAN
    research, script = run.artifacts
    [evaluation] = run.evaluations
    assert (evaluation.status, evaluation.flags, evaluation.evaluator_ref) == (
        EvaluationStatus.PASSED,
        (),
        qa_agent.AGENT_REF,
    )
    assert evaluation.artifact_ref == script.artifact_id
    assert (research.status, script.status) == (ArtifactStatus.DRAFT, ArtifactStatus.CANDIDATE)
    [review] = run.human_reviews
    assert (review.artifact_ref, review.status) == (script.artifact_id, ReviewStatus.PENDING)

    _decide(environ, ReviewStatus.APPROVED)
    restart = _Process(environ, [], [])
    done = restart.resume()

    assert done.status is RunStatus.COMPLETED
    assert done.artifact(script.artifact_id).status is ArtifactStatus.APPROVED
    assert (restart.producer.calls, restart.qa.calls) == ([], [])


def test_s8a_02_the_qa_provider_gets_the_catalogued_prompt_and_the_verdict_shape(
    tmp_path: Path,
) -> None:
    process = _Process(_environ(tmp_path), _producer_turns(), [_verdict("passed", [])])
    run = process.execute()

    prompt = load_prompt_catalogue()[qa_agent.PROMPT_REF]
    [call] = process.qa.calls
    script = run.artifacts[-1]
    assert call["system"] == prompt.system
    assert _user_text(call) == prompt.user_template.replace("{input}", script.content)
    [tool] = cast(list[dict[str, object]], call["tools"])
    schema = cast(dict[str, object], tool["input_schema"])
    assert tool["name"] == "emit_fields"
    assert tuple(cast(dict[str, object], schema["properties"])) == QA_VERDICT_FIELDS
    assert call["tool_choice"] == {"type": "tool", "name": "emit_fields"}
    assert len(process.producer.calls) == 3


def test_s8a_03_the_qa_call_is_recorded_on_the_evaluation_at_the_qa_price(tmp_path: Path) -> None:
    process = _Process(_environ(tmp_path), _producer_turns(), [_verdict("passed", [])])
    run = process.execute()

    *producers, qa = run.analytics_records
    [evaluation] = run.evaluations
    assert [r.agent_ref for r in producers] == [rin.AGENT_REF, rin.AGENT_REF, leo.AGENT_REF]
    assert all(r.task_id is not None and r.evaluation_id is None for r in producers)
    assert (qa.task_id, qa.evaluation_id, qa.agent_ref, qa.prompt_ref) == (
        None,
        evaluation.evaluation_id,
        qa_agent.AGENT_REF,
        _qa_prompt_ref(),
    )
    assert (qa.provider, qa.model) == ("anthropic", "claude-qa-actual")
    assert (qa.token_usage.input_tokens, qa.token_usage.output_tokens) == (2000, 60)
    assert (qa.cost.amount, qa.cost.currency) == (Decimal("0.0023"), "USD")
    assert qa.retries is None


@pytest.mark.parametrize("verdict", ["flagged", "failed"])
def test_s8a_04_a_risk_verdict_stops_the_content_at_the_human_gate(
    tmp_path: Path, verdict: str
) -> None:
    environ = _environ(tmp_path)
    run = _Process(environ, _producer_turns(), [_verdict(verdict, [FLAG])]).execute()

    assert run.status is RunStatus.WAITING_HUMAN
    script = run.artifacts[-1]
    [evaluation] = run.evaluations
    assert (evaluation.status.value, evaluation.flags) == (verdict, (FLAG,))
    [review] = run.human_reviews
    assert (review.artifact_ref, review.status) == (script.artifact_id, ReviewStatus.PENDING)
    idle = _Process(environ, [], [])
    assert idle.stored().snapshot == run.snapshot
    assert idle.resume().status is RunStatus.WAITING_HUMAN
    assert (idle.producer.calls, idle.qa.calls) == ([], [])

    approved = _decide(environ, ReviewStatus.APPROVED)
    with pytest.raises(ArtifactQaNotPassedError):
        approved.transition_artifact(script.artifact_id, ArtifactStatus.APPROVED, by=_CD)
    assert approved.artifact(script.artifact_id).status is ArtifactStatus.CANDIDATE
    still = _Process(environ, [], []).resume()
    assert still.status is RunStatus.WAITING_HUMAN
    assert still.artifact(script.artifact_id).status is ArtifactStatus.CANDIDATE


def test_s8a_05_a_model_flag_drives_rework_and_the_new_version_is_judged_again(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    first = _Process(environ, _producer_turns(), [_verdict("flagged", [FLAG])]).execute()
    v1 = first.artifacts[-1]
    _decide(environ, ReviewStatus.CHANGES_REQUESTED, INSTRUCTIONS)

    restart = _Process(
        environ, [_script_turn("Сцена 1. Подбирайте кроссовки по стопе.")], [_verdict("passed", [])]
    )
    run = restart.resume()

    [leo_call] = restart.producer.calls
    assert leo_call["system"] == load_prompt_catalogue()[leo.PROMPT_REF].system
    rework_input = json.loads(run.tasks[-1].task_input)
    assert rework_input["qa_flags"] == [FLAG]
    assert rework_input["human_instructions"] == INSTRUCTIONS
    assert rework_input["artifact"]["ref"] == v1.artifact_id
    assert run.tasks[-1].task_input in _user_text(leo_call)
    assert len(run.tasks) == 3

    v2 = run.artifacts[-1]
    assert (v2.version, v2.supersedes_ref, v2.status) == (
        2,
        v1.artifact_id,
        ArtifactStatus.CANDIDATE,
    )
    assert run.artifact(v1.artifact_id).status is ArtifactStatus.SUPERSEDED
    [qa_call] = restart.qa.calls
    assert v2.content in _user_text(qa_call)
    flagged, passed = run.evaluations
    assert (flagged.artifact_ref, flagged.status) == (v1.artifact_id, EvaluationStatus.FLAGGED)
    assert (passed.artifact_ref, passed.status) == (v2.artifact_id, EvaluationStatus.PASSED)
    assert run.status is RunStatus.WAITING_HUMAN
    latest = run.human_reviews[-1]
    assert (latest.artifact_ref, latest.status) == (v2.artifact_id, ReviewStatus.PENDING)

    _decide(environ, ReviewStatus.APPROVED)
    done = _Process(environ, [], []).resume()
    assert done.status is RunStatus.COMPLETED
    assert done.artifact(v2.artifact_id).status is ArtifactStatus.APPROVED


def test_s8a_06_a_malformed_verdict_parks_the_gate_and_a_restart_asks_again(
    tmp_path: Path,
) -> None:
    environ = _environ(tmp_path)
    failing = _Process(environ, _producer_turns(), [_verdict("ok", [])])
    with pytest.raises(QaVerdictError):
        failing.execute()

    parked = failing.stored()
    assert parked.status is RunStatus.WAITING_QA
    [pending] = parked.evaluations
    assert pending.status is EvaluationStatus.PENDING
    assert [r.evaluation_id for r in parked.analytics_records][-1] == pending.evaluation_id

    restart = _Process(environ, [], [_verdict("passed", [])])
    run = restart.resume()

    assert restart.producer.calls == []
    assert len(restart.qa.calls) == 1
    assert run.status is RunStatus.WAITING_HUMAN
    [decided] = run.evaluations
    assert (decided.evaluation_id, decided.status) == (
        pending.evaluation_id,
        EvaluationStatus.PASSED,
    )
    qa_records = [r for r in run.analytics_records if r.evaluation_id is not None]
    assert [r.evaluation_id for r in qa_records] == [pending.evaluation_id] * 2


def test_s8a_07_a_completed_run_is_stored_and_resume_calls_no_model(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    _Process(environ, _producer_turns(), [_verdict("passed", [])]).execute()
    _decide(environ, ReviewStatus.APPROVED)
    run = _Process(environ, [], []).resume()
    assert run.status is RunStatus.COMPLETED

    restart = _Process(environ, [], [])
    back = restart.resume()

    assert back.snapshot == run.snapshot
    assert (restart.producer.calls, restart.qa.calls) == ([], [])
