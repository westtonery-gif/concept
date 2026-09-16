"""QA evaluator wiring and the outcome of a QA failure (ADR-0038).

QWR (``EVALUATION_ACCEPTANCE.md`` §4.4): the Composition Root compiles ``qa_agent@v1`` from its real
catalogue assets into an ``LLMArtifactEvaluator``, the compiled Director runs the gate with it, a
failed QA call parks the Run at ``WAITING_QA`` for ``resume`` instead of failing it, and a
model-produced risk verdict drives ADR-0032 rework. Deterministic fakes of the ``LLMClient`` port
only: no SDK, network or system clock.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from omemo_content_factory.agents import qa_agent
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import (
    QA_VERDICT_FIELDS,
    MeasuredEvaluatorError,
    QaCallError,
    QaVerdictError,
)
from omemo_content_factory.composition import (
    CompositionError,
    build_qa_evaluator,
    compile_runtime,
    load_prompt_catalogue,
    validate_qa_evaluator,
)
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.prompt import Prompt, PromptVersion
from omemo_content_factory.domain.run import Actor, Run, RunSnapshot, RunStatus
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.tool import ToolGrantError
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.llm import LLMCallMetrics, LLMCompletion, LLMError
from omemo_content_factory.tools.toolbox import Toolbox

REVIEWER = Actor.HUMAN_REVIEWER
T0 = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
WRITER_REF = "writer@v1"
PASSED = {"verdict": "passed", "flags": "[]"}
FLAGGED = {"verdict": "flagged", "flags": '["нет источника у цифры 70%"]'}


def _turn() -> LLMCallMetrics:
    return LLMCallMetrics(
        provider="test",
        model="qa-model",
        input_tokens=10,
        output_tokens=5,
        cost_amount=Decimal("0.001"),
        cost_currency="USD",
        started_at=T0,
        finished_at=T0,
    )


@dataclass
class ScriptedQaClient:
    """Fake QA model: consumes one scripted answer (fields or an ``LLMError``) per call."""

    answers: list[dict[str, str] | LLMError]
    users: list[str] = field(default_factory=list)
    tools: list[tuple[str, ...]] = field(default_factory=list)

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox
    ) -> LLMCompletion:
        self.users.append(user)
        self.tools.append(tuple(d.tool_id for d in toolbox.descriptors))
        answer = self.answers.pop(0)
        if isinstance(answer, LLMError):
            raise answer
        return LLMCompletion(answer, (_turn(),))


@dataclass
class WriterClient:
    """Fake producer model: every call yields a numbered script and records its input."""

    users: list[str] = field(default_factory=list)

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox
    ) -> LLMCompletion:
        self.users.append(user)
        return LLMCompletion({"script": f"script v{len(self.users)}"}, ())


class SnapshotStore:
    """Whole-Run in-memory store: keeps the last committed snapshot of each Run."""

    def __init__(self) -> None:
        self.saved: dict[str, RunSnapshot] = {}

    def save(self, run: Run) -> None:
        self.saved[run.run_id] = run.snapshot

    def load(self, run_id: str) -> Run | None:
        snapshot = self.saved.get(run_id)
        return None if snapshot is None else Run.restore(snapshot)


def _active_schema(schema_id: str, fields: Sequence[str]) -> Schema:
    schema = Schema.create(
        schema_id=schema_id, version=SchemaVersion(1), description="d", required_fields=fields
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


WRITER = Agent(agent_id=WRITER_REF, name="Writer", prompt_ref="writer")
WRITER_PROMPT = Prompt(
    prompt_id="writer",
    version=PromptVersion(1),
    schema_ref="script@v1",
    system="Пиши сценарий.",
    user_template="{input}",
)
WRITER_SCHEMAS = {"script@v1": _active_schema("script", ["script"])}
WORKFLOW = Workflow.create(
    workflow_id="write@v1",
    name="Write",
    steps=[
        WorkflowStep(
            step_id="write", task_type="write", agent_ref=WRITER_REF, schema_ref="script@v1"
        )
    ],
)
REQUESTS = ContentDirector.expand(WORKFLOW, brief="бриф")


def director(
    qa_client: ScriptedQaClient, writer: WriterClient, store: SnapshotStore | None = None
) -> ContentDirector:
    return compile_runtime(
        [WRITER],
        {"writer": WRITER_PROMPT},
        writer,
        WORKFLOW,
        WRITER_SCHEMAS,
        store=store,
        qa=build_qa_evaluator(qa_agent.QA_AGENT, None, qa_client, qa_agent.SCHEMAS),
    )


def make_run() -> Run:
    return Run.create(run_id="run-qwr-0001", content_brief_ref="b", workflow_version_ref="write@v1")


# --- QWR-01..04: building the evaluator ---------------------------------------------------


def test_qwr_01_the_qa_role_compiles_from_its_bundled_catalogue_entry_without_a_call() -> None:
    client = ScriptedQaClient([])
    prompt = load_prompt_catalogue()[qa_agent.PROMPT_REF]

    evaluator = build_qa_evaluator(qa_agent.QA_AGENT, None, client, qa_agent.SCHEMAS)

    assert (evaluator.evaluator_ref, evaluator.prompt_ref) == ("qa_agent@v1", "qa-agent@v1")
    assert evaluator.output_fields == QA_VERDICT_FIELDS
    assert (evaluator.system_prompt, evaluator.user_template) == (
        prompt.system,
        prompt.user_template,
    )
    assert client.users == []


def test_qwr_02_an_unknown_prompt_or_schema_fails_at_build_time() -> None:
    client = ScriptedQaClient([])
    unknown_prompt = Agent(agent_id="qa@v9", name="QA", prompt_ref="no-such-prompt")

    with pytest.raises(CompositionError, match="unknown prompt"):
        build_qa_evaluator(unknown_prompt, None, client, qa_agent.SCHEMAS)
    with pytest.raises(CompositionError, match="unknown schema"):
        build_qa_evaluator(qa_agent.QA_AGENT, None, client, {})


def test_qwr_03_a_qa_agent_declaring_skills_is_refused() -> None:
    agent = Agent(
        agent_id="qa@v9",
        name="QA",
        prompt_ref=qa_agent.PROMPT_REF,
        skill_refs=("normalize_terminology@v1",),
    )

    with pytest.raises(CompositionError, match="skill"):
        build_qa_evaluator(agent, None, ScriptedQaClient([]), qa_agent.SCHEMAS)


def test_qwr_04_the_model_sees_exactly_the_granted_tools_and_a_bad_grant_fails_early() -> None:
    client = ScriptedQaClient([PASSED])
    granted = Agent(
        agent_id="qa@v9", name="QA", prompt_ref=qa_agent.PROMPT_REF, tool_refs=("text_metrics@v1",)
    )

    build_qa_evaluator(granted, None, client, qa_agent.SCHEMAS).evaluate("текст")

    assert client.tools == [("text_metrics",)]
    ungranted = Agent(
        agent_id="qa@v9", name="QA", prompt_ref=qa_agent.PROMPT_REF, tool_refs=("nope@v1",)
    )
    with pytest.raises(ToolGrantError):
        build_qa_evaluator(ungranted, None, client, qa_agent.SCHEMAS)


# --- QWR-05: the compiled Director --------------------------------------------------------


def test_qwr_05_the_compiled_director_runs_the_gate_with_the_built_evaluator() -> None:
    qa_client = ScriptedQaClient([PASSED])
    run = make_run()

    director(qa_client, WriterClient()).execute(run, REQUESTS)

    assert run.status is RunStatus.COMPLETED
    [evaluation] = run.evaluations
    assert (evaluation.status, evaluation.evaluator_ref) == (EvaluationStatus.PASSED, "qa_agent@v1")
    assert qa_client.users[0].startswith("Материал на проверку:\n")


def test_qwr_05_the_qa_role_as_a_workflow_step_is_refused_at_build_time() -> None:
    evaluator = build_qa_evaluator(qa_agent.QA_AGENT, None, ScriptedQaClient([]), qa_agent.SCHEMAS)
    workflow = Workflow.create(
        workflow_id="bad@v1",
        name="Bad",
        steps=[
            WorkflowStep(
                step_id="judge",
                task_type="qa",
                agent_ref=qa_agent.AGENT_REF,
                schema_ref=qa_agent.SCHEMA_REF,
            )
        ],
    )

    with pytest.raises(CompositionError, match="QA evaluator"):
        validate_qa_evaluator(workflow, evaluator)
    with pytest.raises(CompositionError, match="QA evaluator"):
        compile_runtime(
            [qa_agent.QA_AGENT],
            None,
            WriterClient(),
            workflow,
            qa_agent.SCHEMAS,
            qa=evaluator,
        )


# --- QWR-06: a QA failure parks the Run at the gate ---------------------------------------


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        ({"verdict": "ok", "flags": "[]"}, QaVerdictError),
        (LLMError("provider overloaded", metrics=(_turn(),)), QaCallError),
    ],
)
def test_qwr_06_a_failed_qa_call_leaves_the_run_resumable_and_resume_asks_again(
    failure: dict[str, str] | LLMError, error: type[MeasuredEvaluatorError]
) -> None:
    store = SnapshotStore()
    qa_client = ScriptedQaClient([failure, PASSED])
    writer = WriterClient()
    cd = director(qa_client, writer, store)

    with pytest.raises(error):
        cd.execute(make_run(), REQUESTS)

    parked = store.load("run-qwr-0001")
    assert parked is not None
    assert parked.status is RunStatus.WAITING_QA
    [pending] = parked.evaluations
    assert pending.status is EvaluationStatus.PENDING

    cd.resume(parked, REQUESTS)

    resumed = store.load("run-qwr-0001")
    assert resumed is not None
    assert resumed.status is RunStatus.COMPLETED
    [decided] = resumed.evaluations
    assert (decided.evaluation_id, decided.status) == (
        pending.evaluation_id,
        EvaluationStatus.PASSED,
    )
    assert [r.evaluation_id for r in resumed.analytics_records] == [pending.evaluation_id] * 2
    assert len(writer.users) == 1


# --- QWR-07: a model-produced risk verdict drives rework ----------------------------------


def test_qwr_07_a_model_flag_reaches_the_producer_through_human_requested_rework() -> None:
    store = SnapshotStore()
    qa_client = ScriptedQaClient([FLAGGED, PASSED])
    writer = WriterClient()
    cd = director(qa_client, writer, store)
    run = make_run()

    cd.execute(run, REQUESTS)
    assert run.status is RunStatus.WAITING_HUMAN
    [review] = run.human_reviews
    run.submit_review(
        review.review_id, ReviewStatus.CHANGES_REQUESTED, by=REVIEWER, reason="добавь источник"
    )
    cd.resume(run, REQUESTS)

    rework_input = json.loads(writer.users[1])
    assert rework_input["qa_flags"] == ["нет источника у цифры 70%"]
    assert rework_input["human_instructions"] == "добавь источник"
    first, second = run.evaluations
    assert (first.status, second.status) == (EvaluationStatus.FLAGGED, EvaluationStatus.PASSED)
    assert second.artifact_ref != first.artifact_ref
    assert run.status is RunStatus.WAITING_HUMAN
    latest = run.human_reviews[-1]
    assert (latest.artifact_ref, latest.status) == (second.artifact_ref, ReviewStatus.PENDING)
