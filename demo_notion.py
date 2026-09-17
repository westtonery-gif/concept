"""Demonstration: a brief from Notion drives the real Rin -> Leo -> QA Workflow (ROADMAP Stage 9).

Unlike ``demo_factory.py`` (which hand-wraps a hardcoded brief), this entrypoint reads the brief
from a real Notion database through the ``BriefBoard`` adapter (`ADR-0040`): the core still knows
nothing about Notion beyond the ``BriefBoard`` Protocol it was handed
(`adapters/brief_board.py`, ADR-0023) — everything else (Workflow, agents, QA gate, Run store,
rework) is exactly what ``demo_factory.py`` already exercises, reused from it rather than
duplicated.

Every Run status the Director commits is also shown on the brief's Notion page (ADR-0041): the
Run store is wrapped in a ``BriefStatusReporter``, and the current status is synced once more at
the end of every invocation. A board that refuses a report never stops the Run; the refusal is
printed.

Run with: ``python demo_notion.py <brief-ref>``, where ``<brief-ref>`` is the id of a page in the
configured Notion database. Needs the same ``ANTHROPIC_API_KEY`` / per-role provider+pricing
bindings as ``demo_factory.py``, plus the six ``OMEMO_NOTION_*`` variables
(`infrastructure/notion_brief_board.py`); any missing configuration is explained and the demo
exits cleanly. A brief that is not on the board, not marked ready, or has no text also exits
cleanly (`BriefBoard.fetch_brief` returns ``None`` for all of these — deliberately
indistinguishable, ADR-0040 §3). ``--request-changes "<instructions>"`` plays the human reviewer,
same as in ``demo_factory.py``.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from demo import print_final_article, safe_print, show_run
from demo_factory import (
    AGENTS,
    SCHEMAS,
    WORKFLOW,
    _build_executors,
    _build_qa_evaluator,
    _print_binding_instructions,
    _request_changes,
    _show_model_calls,
    _show_quality_gate,
)

from omemo_content_factory.adapters.brief_board import BriefBoardError
from omemo_content_factory.application.brief_status import BriefStatusReporter
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import MeasuredEvaluatorError
from omemo_content_factory.composition import (
    build_brief_board,
    build_run_store,
    build_schema_map,
    run_store_path,
    validate_qa_evaluator,
    validate_workflow_executors,
)
from omemo_content_factory.domain.run import Run
from omemo_content_factory.infrastructure.provider_model import ProviderModelSelectionError


def _run_id_for(brief_ref: str) -> str:
    """Derive a stable Run id from the brief reference, so a re-run resumes the same Run."""
    return f"run-notion-{brief_ref}"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("brief_ref", help="the Notion page id of the brief to produce")
    parser.add_argument(
        "--request-changes",
        metavar="INSTRUCTIONS",
        help="as the human reviewer, request changes on the stored Run's pending Review, "
        "then resume it (ADR-0032)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Fetch a brief from Notion and run it through the real Rin -> Leo -> QA Workflow."""
    args = _parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        safe_print("ANTHROPIC_API_KEY is not set, so no real model can be called.")
        safe_print("Set it and re-run, e.g.:")
        safe_print('  PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."')
        safe_print("  bash:        export ANTHROPIC_API_KEY=sk-ant-...")
        return

    try:
        board = build_brief_board(os.environ)
        executors = _build_executors()
        qa = _build_qa_evaluator()
    except BriefBoardError as exc:
        safe_print(f"The Notion board is not configured: {exc}")
        return
    except ProviderModelSelectionError as exc:
        safe_print(f"Provider/model selection failed: {exc}")
        safe_print("Each role needs its own binding and pricing (ADR-0016/0029). Set:")
        _print_binding_instructions()
        return

    validate_workflow_executors(WORKFLOW, executors)
    validate_qa_evaluator(WORKFLOW, qa)
    store = BriefStatusReporter(build_run_store(os.environ), board)
    director = ContentDirector(
        executors, build_schema_map(AGENTS, None, SCHEMAS), qa=qa, store=store
    )

    run_id = _run_id_for(args.brief_ref)
    safe_print("=" * 78)
    safe_print(f"Notion brief '{args.brief_ref}' -> Rin -> Leo -> QA")
    safe_print("=" * 78)
    run = store.load(run_id)
    if args.request_changes is not None:
        if not _request_changes(run, args.request_changes):
            return
        assert run is not None
        store.save(run)
    try:
        if run is None:
            brief = board.fetch_brief(args.brief_ref)
            if brief is None:
                safe_print(
                    f"No producible brief '{args.brief_ref}' on the board — "
                    "not found, not ready, or has no text."
                )
                return
            run = Run.create(
                run_id=run_id,
                content_brief_ref=brief.brief_ref,
                workflow_version_ref=WORKFLOW.workflow_id,
            )
            director.execute_workflow(run, WORKFLOW, brief=brief.body)
        else:
            location = run_store_path(os.environ)
            safe_print(f"Resuming {run_id} from {location} at '{run.status.value}';")
            safe_print("committed steps are not run again. Delete that file to start over.")
            director.resume_workflow(run, WORKFLOW, brief="")
    except MeasuredEvaluatorError as exc:
        safe_print(f"QA gave no verdict ({type(exc).__name__}: {exc}).")
        safe_print(
            "The Run stays at WAITING_QA with its Evaluation PENDING; run again to ask QA again."
        )
    store.sync(run)
    show_run(run)
    _show_quality_gate(run)
    _show_model_calls(run)
    print_final_article(run)
    _show_board_reports(store, run)


def _show_board_reports(store: BriefStatusReporter, run: Run) -> None:
    """Say what the Notion page shows now, and every report the board refused (ADR-0041 §3)."""
    safe_print("")
    for failed in store.failed_reports:
        safe_print(
            f"Notion refused status '{failed.status.value}' of {failed.run_id}: {failed.message}"
        )
    if store.shown_status(run.run_id) is run.status:
        safe_print(f"The Notion page shows '{run.status.value}' for {run.run_id}.")
    else:
        safe_print("The Notion page may show an older status; run again to retry the report.")


if __name__ == "__main__":
    main()
