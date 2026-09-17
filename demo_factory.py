"""Demonstration: the two migrated production roles (Rin, Leo) running together for real.

The script Leo writes then goes through the fail-closed QA gate, judged by the real QA role
(``qa_agent@v1``, ADR-0035/0036/0038).

Runs the ``research-to-script@v1`` Workflow — Rin (``content_researcher@v1``) then Leo
(``script_writer@v1``) — through the real Composition Root (`ADR-0012`/`ADR-0013`) and the real
Run/Task/Output/Artifact domain, backed by a live Anthropic model. Unlike ``demo.py`` (which
hand-wrote its own one-off Research/Writer/Editor prompts before either role was migrated), this
demo exercises the **actual catalogued Agent + Prompt + Schema assets** that
``tests/test_content_researcher_agent.py``, ``tests/test_script_writer_agent.py`` and
``tests/test_research_to_script_workflow.py`` already prove correct in isolation and in a keyless
run.

Each role gets **its own** configured provider/model via :func:`client_for_role` (`ADR-0016`,
`infrastructure/provider_model.py`) — the Composition Root's ``build_executor_map`` is called once
per agent with that role's own client and the results merged, since the Root itself still takes one
shared client per call (`ADR-0012`, unchanged) and per-role selection is a caller concern (SPEC §4).
There is no shared/default model here: a role with no ``OMEMO_PROVIDER__<ROLE>`` binding fails
closed (`ProviderModelSelectionError`), matching this repo's earlier ad-hoc demos never having a
silent hardcoded fallback either.

Run with: ``python demo_factory.py``. Needs ``ANTHROPIC_API_KEY`` plus a per-role provider/model
binding and exact input/output token prices + currency for each of Rin and Leo (see the printed
instructions, or README.md); missing values make the demo explain what to set and exit cleanly.
The QA role is bound and priced the same way. Every candidate stops at ``WAITING_HUMAN`` with a
Review opened — a ``PASSED`` one for approval, a risk verdict as an escalation (ADR-0044). Play the
human reviewer with ``python demo_factory.py --approve``, which approves a passed candidate and
completes the Run, or ``python demo_factory.py --request-changes "<instructions>"``: it requests
changes on that Review and resumes, so Leo reworks the script from the model's flags and QA judges
the new version (ADR-0032); ``--reject "<reason>"`` reworks it the same way, carrying the reason
(ADR-0045). A failed QA call leaves the Run at ``WAITING_QA``; running again asks
QA again (ADR-0038 §1). Publication and external integrations are not involved.

The Run is saved after every step to the Run store (`ADR-0026`; ``OMEMO_RUN_STORE_PATH``, default
``.omemo/runs.sqlite3``). Running the demo again resumes the stored Run instead of starting over:
an interrupted run continues where it stopped, a finished one is only shown — no model is called
for work already committed.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from demo import print_final_article, safe_print, show_run

from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import qa_agent
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import (
    ArtifactEvaluator,
    MeasuredEvaluatorError,
)
from omemo_content_factory.application.review_publication import latest_qa
from omemo_content_factory.application.task_execution import TaskExecutor
from omemo_content_factory.composition import (
    build_executor_map,
    build_qa_evaluator,
    build_run_store,
    build_schema_map,
    run_store_path,
    validate_qa_evaluator,
    validate_workflow_executors,
)
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.domain.run import Actor, Run, RunStatus
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.provider_model import (
    ProviderModelSelectionError,
    client_for_role,
)

RUN_ID = "run-magnesium-sleep-001"

BRIEF = (
    "Тема: почему магний важен для сна и восстановления, для взрослой аудитории. "
    "Цель: короткий вертикальный видео-сценарий, спокойный и доказательный тон, без алармизма."
)

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


def _env_token(agent_ref: str) -> str:
    """Mirror ``infrastructure/provider_model.py``'s role -> env-token normalization (SPEC §1.2)."""
    return "".join(ch if ch.isalnum() else "_" for ch in agent_ref).upper()


def _print_binding_instructions() -> None:
    """Print every per-role provider/model/pricing variable needed for a real client."""
    for agent in (*AGENTS, *qa_agent.AGENTS):
        token = _env_token(agent.agent_id)
        safe_print(f"  export OMEMO_PROVIDER__{token}=anthropic")
        safe_print(f"  export OMEMO_MODEL__{token}=claude-sonnet-4-6")
        safe_print(
            f"  export OMEMO_INPUT_PRICE_PER_MILLION__{token}=REPLACE_WITH_CURRENT_DECIMAL_RATE"
        )
        safe_print(
            f"  export OMEMO_OUTPUT_PRICE_PER_MILLION__{token}=REPLACE_WITH_CURRENT_DECIMAL_RATE"
        )
        safe_print(f"  export OMEMO_PRICE_CURRENCY__{token}=USD")


def _build_executors() -> dict[str, TaskExecutor]:
    """Resolve each role's own provider/model via `client_for_role` (ADR-0016) — no shared client.

    `build_executor_map` takes one client per call, so each agent is compiled on its own (a
    single-element agents list) with its own resolved client, and the resulting one-entry maps are
    merged — the Composition Root itself is unchanged (ADR-0012); this is caller-side per-role
    selection (PROVIDER_MODEL_SPEC §4).
    """
    executors: dict[str, TaskExecutor] = {}
    for agent in AGENTS:
        role_client = client_for_role(agent.agent_id, os.environ)
        executors.update(
            build_executor_map(
                [agent],
                None,
                role_client,
                SCHEMAS,
                skill_invocations=SKILL_INVOCATIONS,
            )
        )
    return executors


def _build_qa_evaluator() -> ArtifactEvaluator:
    """Compile the QA role from its catalogue entry on its own provider/model (ADR-0038 §2)."""
    return build_qa_evaluator(
        qa_agent.QA_AGENT,
        None,
        client_for_role(qa_agent.AGENT_REF, os.environ),
        qa_agent.SCHEMAS,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    add_reviewer_arguments(parser)
    return parser.parse_args(argv)


def add_reviewer_arguments(parser: argparse.ArgumentParser) -> None:
    """The three ways to play the human reviewer on the stored Run's pending Review."""
    decision = parser.add_mutually_exclusive_group()
    decision.add_argument(
        "--request-changes",
        metavar="INSTRUCTIONS",
        help="as the human reviewer, request changes on the stored Run's pending Review, "
        "then resume it (ADR-0032)",
    )
    decision.add_argument(
        "--approve",
        action="store_true",
        help="as the human reviewer, approve the stored Run's pending Review, then resume it; "
        "only a candidate that passed QA completes (ADR-0044)",
    )
    decision.add_argument(
        "--reject",
        metavar="REASON",
        help="as the human reviewer, reject the stored Run's pending Review with a reason, then "
        "resume it; the candidate is reworked with that reason (ADR-0045)",
    )


def _play_reviewer(run: Run | None, args: argparse.Namespace) -> bool:
    """Apply ``--approve`` / ``--request-changes`` / ``--reject`` to ``run``; whether submitted."""
    if args.approve:
        return _approve(run)
    if args.request_changes is not None:
        return _decide(run, ReviewStatus.CHANGES_REQUESTED, args.request_changes)
    if args.reject is not None:
        return _decide(run, ReviewStatus.REJECTED, args.reject)
    return False


def plays_reviewer(args: argparse.Namespace) -> bool:
    """Whether a manual reviewer decision flag was given."""
    return bool(args.approve) or args.request_changes is not None or args.reject is not None


def _approve(run: Run | None) -> bool:
    """Submit ``APPROVED`` on the pending Review; whether it was submitted."""
    pending = _pending_review(run)
    if run is None or pending is None:
        return False
    evaluation = latest_qa(run, run.human_review(pending).artifact_ref)
    if evaluation is None or evaluation.status is not EvaluationStatus.PASSED:
        safe_print(f"{pending} is on a candidate QA did not pass; it cannot be approved.")
        safe_print("Request changes or reject it instead (ADR-0045 §3).")
        return False
    run.submit_review(pending, ReviewStatus.APPROVED, by=Actor.HUMAN_REVIEWER)
    safe_print(f"Approved {pending}.")
    return True


def _pending_review(run: Run | None) -> str | None:
    """The id of the stored Run's pending Review, or ``None`` (with the reason printed)."""
    if run is None:
        safe_print(f"No stored {RUN_ID} to review; run the demo without arguments first.")
        return None
    pending = [view for view in run.human_reviews if view.status is ReviewStatus.PENDING]
    if run.status is not RunStatus.WAITING_HUMAN or not pending:
        safe_print(f"{RUN_ID} is '{run.status.value}' with no pending Review; nothing to decide.")
        return None
    return pending[-1].review_id


def _decide(run: Run | None, decision: ReviewStatus, reason: str) -> bool:
    """Submit ``CHANGES_REQUESTED`` / ``REJECTED`` with ``reason``; whether it was submitted."""
    pending = _pending_review(run)
    if run is None or pending is None:
        return False
    run.submit_review(pending, decision, by=Actor.HUMAN_REVIEWER, reason=reason)
    safe_print(f"Decided {decision.value} on {pending}: {reason}")
    return True


def main(argv: Sequence[str] | None = None) -> None:
    """Run the Rin -> Leo Workflow and its QA gate, each role on its own provider/model."""
    args = _parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        safe_print("ANTHROPIC_API_KEY is not set, so no real model can be called.")
        safe_print("Set it and re-run, e.g.:")
        safe_print('  PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."')
        safe_print("  bash:        export ANTHROPIC_API_KEY=sk-ant-...")
        return

    try:
        executors = _build_executors()
        qa = _build_qa_evaluator()
    except ProviderModelSelectionError as exc:
        safe_print(f"Provider/model selection failed: {exc}")
        safe_print("Each role needs its own binding and pricing (ADR-0016/0029). Set:")
        _print_binding_instructions()
        return

    validate_workflow_executors(WORKFLOW, executors)
    validate_qa_evaluator(WORKFLOW, qa)
    store = build_run_store(os.environ)
    director = ContentDirector(
        executors, build_schema_map(AGENTS, None, SCHEMAS), qa=qa, store=store
    )

    safe_print("=" * 78)
    safe_print("Rin -> Leo -> QA, the real catalogued roles, each on its own provider/model")
    safe_print("=" * 78)
    run = store.load(RUN_ID)
    if plays_reviewer(args):
        if not _play_reviewer(run, args):
            return
        assert run is not None
        store.save(run)
    try:
        if run is None:
            run = Run.create(
                run_id=RUN_ID,
                content_brief_ref="brief-magnesium-sleep",
                workflow_version_ref=WORKFLOW.workflow_id,
            )
            director.execute_workflow(run, WORKFLOW, brief=BRIEF)
        else:
            location = run_store_path(os.environ)
            safe_print(f"Resuming {RUN_ID} from {location} at '{run.status.value}';")
            safe_print("committed steps are not run again. Delete that file to start over.")
            director.resume_workflow(run, WORKFLOW, brief=BRIEF)
    except MeasuredEvaluatorError as exc:
        safe_print(f"QA gave no verdict ({type(exc).__name__}: {exc}).")
        safe_print(
            "The Run stays at WAITING_QA with its Evaluation PENDING; run again to ask QA again."
        )
    show_run(run)
    _show_quality_gate(run)
    _show_model_calls(run)
    print_final_article(run)


def _show_quality_gate(run: Run) -> None:
    """Print each QA Evaluation and Human Review — what the Stage 8 live check reads (ADR-0038)."""
    safe_print("  QA Evaluations:")
    for evaluation in run.evaluations:
        safe_print(
            f"    - {evaluation.evaluation_id} on {evaluation.artifact_ref} "
            f"[{evaluation.status.value}] by {evaluation.evaluator_ref}"
        )
        for flag in evaluation.flags:
            safe_print(f"        flag: {flag}")
    safe_print("  Human Reviews:")
    for review in run.human_reviews:
        reason = "" if review.reason is None else f" — {review.reason}"
        safe_print(
            f"    - {review.review_id} on {review.artifact_ref} [{review.status.value}]{reason}"
        )
    if run.status is RunStatus.WAITING_HUMAN and any(
        review.status is ReviewStatus.PENDING for review in run.human_reviews
    ):
        safe_print("  To approve:         python demo_factory.py --approve")
        safe_print(
            '  To request changes: python demo_factory.py --request-changes "<instructions>"'
        )
        safe_print('  To reject:          python demo_factory.py --reject "<reason>"')


def _show_model_calls(run: Run) -> None:
    """Print every captured model call (ADR-0029) — what the M2 live check reads (ADR-0033 §4)."""
    safe_print("  Model calls (Analytics Records):")
    for record in run.analytics_records:
        usage = record.token_usage
        span = record.time_range.finished_at - record.time_range.started_at
        safe_print(
            f"    - {record.task_id or record.evaluation_id} {record.prompt_ref} "
            f"{record.provider}/{record.model} "
            f"tokens={usage.input_tokens}+{usage.output_tokens} "
            f"cost={record.cost.amount} {record.cost.currency} "
            f"latency={span.total_seconds() * 1000:.0f}ms retries={record.retries}"
        )


if __name__ == "__main__":
    main()
