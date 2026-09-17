# ADR-0048: One brief invocation as application code, and listing stored Runs by status

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect (the sweep chosen by the maintainer)
- **Realizes:** ROADMAP Этап 11 (`CLAUDE.md` queue 15.2)

## Context

Stage 11 puts n8n in front of the core (ARCHITECTURE §3.2, §12). The maintainer chose (2026-09-17)
that the core exposes an HTTP service n8n calls (queue 15.3), and that n8n, on a schedule, asks the
core to **sweep** Runs waiting for a human — a Google Doc has no "decided" event.

Both requests need code that today exists only inside `demo_notion.py`'s `main`:

1. the per-brief flow — read the reviewer's decision from the desk (ADR-0046 `take_review_decision`),
   produce or resume the brief (ADR-0042 `produce_brief`), publish the pending review (ADR-0044) and
   show its link on the brief (ADR-0047) — with each desk refusal reported, not raised;
2. the `run_id` a brief gets (`run-notion-<brief_ref>`, a private helper of the demo);
3. for the sweep, *which* briefs have a Run waiting for a human. `RunStore` can only `load` a known
   id; listing stored Runs was deferred by ADR-0024 and ADR-0026 until an entrypoint needed it.
   The sweep is that entrypoint.

ADR-0046 declined a single invocation function because it "would have to report two independent desk
outcomes plus the intake's, for one caller". There are now two callers — the CLI and the service —
and the service must not print. That trade-off has turned.

## Decision

### 1. `application/brief_production.py`

```python
run_id_for_brief(brief_ref) -> str            # "run-notion-" + brief_ref (unchanged spelling)

@dataclass(frozen=True, slots=True)
class BriefInvocation:
    brief_ref: str
    run_id: str
    run: Run | None                           # None: no producible brief and no stored Run
    decision: FetchedDecision | None = None
    decision_error: str | None = None         # the desk refused to give the decision
    qa_error: str | None = None               # QA gave no verdict; the Run waits at waiting_qa
    published: PublishedReview | None = None
    publish_error: str | None = None          # the desk refused to publish

class BriefProduction:
    def __init__(self, director, store: BriefStatusReporter, board, workflow, *,
                 desk: ReviewDesk | None = None, index: RunIndex | None = None) -> None
    def invoke(self, brief_ref: str) -> BriefInvocation
    def waiting_briefs(self) -> tuple[str, ...]
```

`invoke(brief_ref)`, in order — the order ADR-0045 §4 / ADR-0046 fixed:

1. with a desk: `take_review_decision(store, desk, run_id)`; a `ReviewDeskError` becomes
   `decision_error` and the invocation goes on (the Run keeps waiting; a resume leaves it alone);
2. `produce_brief(director, store, board, workflow, brief_ref=, run_id=)`; a
   `MeasuredEvaluatorError` becomes `qa_error` and the Run is loaded back from the store;
3. with a desk and a Run: `publish_pending_review(run, desk)`; a `ReviewDeskError` becomes
   `publish_error`; a publication is shown on the brief with `store.show_review_location`.

Everything else propagates: `BriefIntakeError`, `BriefBoardError` from `fetch_brief`,
`RunStoreError`, domain errors, defects. They are not a "desk said no" but a brief that cannot be
produced or a broken system, and the caller decides how to surface them (the demo prints, the
service logs).

The `run_id` keeps the `run-notion-` spelling so Runs already stored by `demo_notion.py` are resumed,
not duplicated.

### 2. `RunIndex` — an additive storage contract

```python
class RunIndex(Protocol):
    def run_ids(self, /, *, status: RunStatus) -> tuple[str, ...]:
        """Ids of the stored Runs whose stored status is ``status``, in ascending id order."""
```

It lives in `adapters/run_store.py` next to `RunStore`, which is **unchanged**: a wrapper such as
`BriefStatusReporter` stays a plain `RunStore`, and only the component that sweeps needs an index.
Failures are `RunStoreError`, like the store's.

`SqliteRunStore` implements it by reading every row in id order and decoding its snapshot with the
same codec `load` uses; an undecodable row or a row holding another Run is `RunStoreError` — the same
strictness as `load`, so a sweep never skips a Run silently. It does not restore the Run (no domain
check), and it adds no column (no migration of existing files). Reading every snapshot on each sweep
is linear in the store; a status column is the known next step if that ever matters.
`composition.build_run_index(environ)` builds it over the same file as `build_run_store`.

### 3. `waiting_briefs()`

The briefs of stored Runs at `waiting_human`: for each id from `index.run_ids(status=WAITING_HUMAN)`
the Run is loaded and its `content_brief_ref` taken — **only** when the id is
`run_id_for_brief(content_brief_ref)`. A Run the brief intake did not create (e.g. `demo_factory.py`
writes to the same default file) is not a brief's Run and is skipped. Without an index it is a
`ValueError` to call; a Run that disappears between listing and loading is skipped.

### 4. `demo_notion.py` delegates

The demo applies a manual reviewer flag as before, then calls `BriefProduction.invoke` and prints
the `BriefInvocation`.

## Consequences

### Positive

- The CLI and the HTTP service run the same per-brief code; the flow is tested without a printer.
- The sweep can find its Runs without a new column or a format change.

### Negative / Trade-offs

- `BriefInvocation` carries string messages for desk refusals rather than the exceptions; the
  exception types are not needed by any caller, and a frozen value is easy to log and assert.
- A sweep decodes every stored snapshot.

## Alternatives considered

- **List Runs through `RunStore`** — rejected: every `RunStore` wrapper would have to forward it.
- **A `status` column** — rejected for now: a schema migration of existing files for a small store.
- **Keep the flow in the demo and duplicate it in the service** — rejected (two copies of ADR-0045 §4's
  order).

## References

- `ROADMAP.md`: Этап 11; `ARCHITECTURE.md` §3.2, §3.3, §12
- ADR-0024 "Deferred", ADR-0026, ADR-0042, ADR-0044, ADR-0045, ADR-0046, ADR-0047
- `ADAPTER_SPEC.md` §4, §5; `ADAPTER_ACCEPTANCE.md` §1, §5, §13
