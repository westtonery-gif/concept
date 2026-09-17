"""Brief intake — a brief on the board becomes a produced Run (ROADMAP Stage 9, ADR-0042).

``produce_brief`` is the flow an entrypoint runs for one brief: fetch it, create its Run and
execute the Workflow — or resume the Run already stored for it — and finally show the Run's status
on the board. It knows the board only through the ``BriefBoard`` Protocol and the write-back only
through ``BriefStatusReporter``; nothing here is specific to Notion.
"""

from __future__ import annotations

from omemo_content_factory.adapters.brief_board import BriefBoard
from omemo_content_factory.application.brief_status import BriefStatusReporter
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import MeasuredEvaluatorError
from omemo_content_factory.domain.run import Run
from omemo_content_factory.domain.workflow import Workflow


class BriefIntakeError(Exception):
    """A stored Run cannot be produced from this brief; the Run was left as stored."""


def produce_brief(
    director: ContentDirector,
    store: BriefStatusReporter,
    board: BriefBoard,
    workflow: Workflow,
    *,
    brief_ref: str,
    run_id: str,
) -> Run | None:
    """Produce the brief ``brief_ref`` as the Run ``run_id``; ``None`` if there is nothing to do.

    ``director`` must commit through ``store``, and ``store`` report to ``board``. Without a stored
    Run the brief is fetched: an unproducible one returns ``None`` and starts nothing; otherwise a
    new Run executes ``workflow`` on the brief's text. A stored Run must belong to ``brief_ref``
    and is resumed (ADR-0042 §2). After a normal return — and when QA gave no verdict
    (``MeasuredEvaluatorError``, the Run is stored at ``waiting_qa``) — the current status is synced
    to the board; any other exception propagates without a sync.
    """
    stored = store.load(run_id)
    if stored is None:
        brief = board.fetch_brief(brief_ref)
        if brief is None:
            return None
        run = Run.create(
            run_id=run_id,
            content_brief_ref=brief.brief_ref,
            workflow_version_ref=workflow.workflow_id,
        )
        text = brief.body
    else:
        if stored.content_brief_ref != brief_ref:
            raise BriefIntakeError(
                f"run {run_id} belongs to brief {stored.content_brief_ref}, not {brief_ref}"
            )
        run = stored
        text = _resume_brief(run, board)
    try:
        if stored is None:
            director.execute_workflow(run, workflow, brief=text)
        else:
            director.resume_workflow(run, workflow, brief=text)
    except MeasuredEvaluatorError:
        store.sync(run)
        raise
    store.sync(run)
    return run


def _resume_brief(run: Run, board: BriefBoard) -> str:
    """The brief a resumed Run's first step gets (ADR-0042 §2).

    A started first Task keeps the brief it was given. A Run stored before its first Task has used
    no brief text yet, so the board's current brief is fetched again; if it is no longer producible
    the Run cannot start.
    """
    if run.tasks:
        return run.tasks[0].task_input
    brief = board.fetch_brief(run.content_brief_ref)
    if brief is None:
        raise BriefIntakeError(
            f"run {run.run_id} has not started a step and brief {run.content_brief_ref} is no "
            f"longer producible; it stays at '{run.status.value}'"
        )
    return brief.body
