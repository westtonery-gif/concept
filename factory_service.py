"""The factory as a service: n8n triggers briefs over HTTP (ROADMAP Stage 11, ADR-0049).

Builds exactly the production path ``demo_notion.py`` runs (``build_brief_production``: the Notion
board, the optional Google Docs desk, Rin -> Leo -> QA, the SQLite Run store with status write-back)
and serves it with ``ProductionService``:

- ``POST /v1/briefs`` ``{"brief_ref": "<notion page id>"}`` — produce or advance that brief;
- ``POST /v1/reviews/sweep`` — advance every brief whose Run waits for a human (decisions typed into
  the review Docs are read here);
- ``GET /v1/health``.

Both POSTs need ``Authorization: Bearer $OMEMO_SERVICE_TOKEN`` and answer ``202`` at once; one
background worker produces one brief at a time and logs a line per invocation. The n8n workflows
that call these routes are in ``n8n/`` (see ``n8n/README.md``).

Run with: ``python factory_service.py``. Needs everything ``demo_notion.py`` needs plus
``OMEMO_SERVICE_TOKEN`` (at least 32 characters); ``OMEMO_SERVICE_HOST`` / ``OMEMO_SERVICE_PORT``
default to ``127.0.0.1:8765``. Stop with Ctrl+C.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Sequence
from types import FrameType

from demo import safe_print
from demo_notion import build_brief_production, explain_configuration_error

from omemo_content_factory.adapters.brief_board import BriefBoardError
from omemo_content_factory.adapters.review_desk import ReviewDeskError
from omemo_content_factory.application.brief_production import BriefInvocation, BriefProduction
from omemo_content_factory.composition import build_production_service
from omemo_content_factory.infrastructure.production_service import ServiceConfigurationError
from omemo_content_factory.infrastructure.provider_model import ProviderModelSelectionError
from omemo_content_factory.log import configure_logging

_LOG = logging.getLogger("factory_service")


def log_invocation(invocation: BriefInvocation) -> None:
    """One line per invocation; desk refusals and QA without a verdict as warnings."""
    run = invocation.run
    if run is None:
        _LOG.info(
            "brief %s: nothing to produce (not ready, unknown or empty)", invocation.brief_ref
        )
        return
    _LOG.info("brief %s: run %s is %s", invocation.brief_ref, run.run_id, run.status.value)
    if invocation.decision is not None:
        applied = "recorded" if invocation.decision.applied else "not recorded (QA did not pass)"
        _LOG.info(
            "brief %s: reviewer decision %s %s",
            invocation.brief_ref,
            invocation.decision.decision.value,
            applied,
        )
    if invocation.published is not None:
        _LOG.info("brief %s: review at %s", invocation.brief_ref, invocation.published.location)
    for label, message in (
        ("the desk refused the decision", invocation.decision_error),
        ("QA gave no verdict", invocation.qa_error),
        ("the desk refused to publish", invocation.publish_error),
    ):
        if message is not None:
            _LOG.warning("brief %s: %s: %s", invocation.brief_ref, label, message)


def logged(production: BriefProduction) -> BriefProduction:
    """``production`` whose ``invoke`` also logs what each invocation did."""
    invoke = production.invoke

    def invoke_and_log(brief_ref: str) -> BriefInvocation:
        invocation = invoke(brief_ref)
        log_invocation(invocation)
        return invocation

    production.invoke = invoke_and_log  # type: ignore[method-assign]
    return production


def main(argv: Sequence[str] | None = None) -> int:
    """Serve the factory until interrupted; a configuration problem exits with status 2."""
    configure_logging(os.environ.get("OMEMO_LOG_LEVEL", "INFO"))
    try:
        production = logged(build_brief_production(os.environ))
        service = build_production_service(os.environ, production)
    except (BriefBoardError, ReviewDeskError, ProviderModelSelectionError) as exc:
        explain_configuration_error(exc)
        return 2
    except ServiceConfigurationError as exc:
        safe_print(f"The production service is not configured: {exc}")
        return 2

    stopping = threading.Event()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    service.start()
    host, port = service.address
    safe_print(f"Concept factory service on http://{host}:{port} (Ctrl+C to stop)")
    stopping.wait()
    safe_print("Stopping: the running brief finishes, queued ones are dropped.")
    service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
