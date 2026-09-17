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
indistinguishable, ADR-0040 §3). ``--approve`` / ``--request-changes "<instructions>"`` /
``--reject "<reason>"`` play the human reviewer, same as in ``demo_factory.py``.

When both ``OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE`` and ``OMEMO_GOOGLE_REVIEW_FOLDER_ID`` are set, a Run
waiting for a human has its pending review published as a Google Doc at the end of every invocation
(ADR-0043/0044) and the Doc's link is printed; publishing again finds the same Doc. Without them the
review stays in the Run store only; a half-configured desk is explained and the demo exits. A desk
that refuses to publish never touches the Run — the refusal is printed and the next invocation
tries again.

With the desk configured and no reviewer flag given, a stored Run waiting for a human first has its
review published (idempotent) and the reviewer's decision read from the Doc (ADR-0045): approved,
changes requested or rejected is recorded, saved and routed by the resume that follows — a rework
publishes the new version's review at the end. An approval of a candidate QA did not pass is not
recorded; the demo says to change the Doc's decision instead. A reviewer flag wins over the Doc.
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
    _play_reviewer,
    _print_binding_instructions,
    _show_model_calls,
    _show_quality_gate,
    add_reviewer_arguments,
    plays_reviewer,
)

from omemo_content_factory.adapters.brief_board import BriefBoardError
from omemo_content_factory.adapters.review_desk import ReviewDesk, ReviewDeskError
from omemo_content_factory.application.brief_intake import BriefIntakeError, produce_brief
from omemo_content_factory.application.brief_status import BriefStatusReporter
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import MeasuredEvaluatorError
from omemo_content_factory.application.review_decision import apply_review_decision
from omemo_content_factory.application.review_publication import publish_pending_review
from omemo_content_factory.composition import (
    build_brief_board,
    build_review_desk,
    build_run_store,
    build_schema_map,
    run_store_path,
    validate_qa_evaluator,
    validate_workflow_executors,
)
from omemo_content_factory.domain.run import Run, RunStatus
from omemo_content_factory.infrastructure.provider_model import ProviderModelSelectionError

_GOOGLE_VARS = ("OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE", "OMEMO_GOOGLE_REVIEW_FOLDER_ID")


def _run_id_for(brief_ref: str) -> str:
    """Derive a stable Run id from the brief reference, so a re-run resumes the same Run."""
    return f"run-notion-{brief_ref}"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("brief_ref", help="the Notion page id of the brief to produce")
    add_reviewer_arguments(parser)
    return parser.parse_args(argv)


def _build_review_desk() -> ReviewDesk | None:
    """The Google Docs desk when configured, ``None`` when not configured at all (ADR-0044 §5).

    A partly or wrongly configured desk raises ``ReviewDeskError`` naming what is wrong.
    """
    if not any(os.environ.get(name, "").strip() for name in _GOOGLE_VARS):
        return None
    return build_review_desk(os.environ)


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
        desk = _build_review_desk()
        executors = _build_executors()
        qa = _build_qa_evaluator()
    except BriefBoardError as exc:
        safe_print(f"The Notion board is not configured: {exc}")
        return
    except ReviewDeskError as exc:
        safe_print(f"The Google Docs review desk is not configured: {exc}")
        safe_print(f"Set both {' and '.join(_GOOGLE_VARS)}, or neither to keep reviews local.")
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
    if not _prepare_stored_run(store, run_id, args, desk):
        return
    try:
        run = produce_brief(
            director, store, board, WORKFLOW, brief_ref=args.brief_ref, run_id=run_id
        )
    except BriefIntakeError as exc:
        safe_print(f"Cannot produce brief '{args.brief_ref}': {exc}")
        return
    except MeasuredEvaluatorError as exc:
        safe_print(f"QA gave no verdict ({type(exc).__name__}: {exc}).")
        safe_print(
            "The Run stays at WAITING_QA with its Evaluation PENDING; run again to ask QA again."
        )
        run = store.load(run_id)
        assert run is not None
    if run is None:
        safe_print(
            f"No producible brief '{args.brief_ref}' on the board — "
            "not found, not ready, or has no text."
        )
        return
    show_run(run)
    _show_quality_gate(run)
    _show_model_calls(run)
    print_final_article(run)
    _show_board_reports(store, run)
    _publish_review(desk, run)


def _prepare_stored_run(
    store: BriefStatusReporter, run_id: str, args: argparse.Namespace, desk: ReviewDesk | None
) -> bool:
    """Apply a reviewer decision to the stored Run, or announce a resume; whether to go on."""
    stored = store.load(run_id)
    if plays_reviewer(args):
        if not _play_reviewer(stored, args):
            return False
        assert stored is not None
        store.save(stored)
    elif stored is not None:
        location = run_store_path(os.environ)
        safe_print(f"Resuming {run_id} from {location} at '{stored.status.value}';")
        safe_print("committed steps are not run again. Delete that file to start over.")
        if desk is not None and _read_decision(desk, stored):
            store.save(stored)
    return True


def _read_decision(desk: ReviewDesk, run: Run) -> bool:
    """Read the reviewer's decision from the desk into ``run``; whether it was recorded (ADR-0045).

    The pending review is published first, so one a crash left unpublished can be read at all.
    """
    try:
        publish_pending_review(run, desk)
        fetched = apply_review_decision(run, desk)
    except ReviewDeskError as exc:
        safe_print(f"Google Docs refused to give the reviewer's decision: {exc}")
        safe_print("The Run keeps waiting; run again to retry.")
        return False
    if fetched is None:
        if run.status is RunStatus.WAITING_HUMAN:
            safe_print("The reviewer has not decided yet; the Run keeps waiting.")
        return False
    if not fetched.applied:
        safe_print(
            f"The Doc approves {fetched.review_id}, but QA did not pass this candidate, so it "
            "cannot be approved."
        )
        safe_print("Change the Doc's decision to 'доработать' or 'отклонено' (ADR-0045 §3).")
        return False
    reason = "" if fetched.reason is None else f": {fetched.reason}"
    safe_print(f"The Doc decides {fetched.decision.value} on {fetched.review_id}{reason}")
    return True


def _publish_review(desk: ReviewDesk | None, run: Run) -> None:
    """Put the Run's pending review on the desk and say where it is (ADR-0044 §4)."""
    if desk is None:
        if run.status is RunStatus.WAITING_HUMAN:
            safe_print("No review desk is configured; the review stays in the Run store.")
        return
    try:
        published = publish_pending_review(run, desk)
    except ReviewDeskError as exc:
        safe_print(f"Google Docs refused to publish the review: {exc}")
        safe_print("The Run is unchanged; run again to retry the publication.")
        return
    if published is not None:
        safe_print(f"Review {published.review_id} is on Google Docs: {published.location}")


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
