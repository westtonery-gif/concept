# ADR-0047: The review Doc's link is shown on the brief

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect (the write path chosen by the maintainer)
- **Realizes:** ROADMAP Этап 11 (`CLAUDE.md` queue 15.1)

## Context

ROADMAP Stage 11 asks that "statuses **and links** are carried between the Edge systems
automatically". Statuses already are: `BriefStatusReporter` shows every committed Run status on the
Notion brief (ADR-0041). Links are not. `ReviewDesk.publish` returns where the review is (a Google
Doc URL, ADR-0043), `publish_pending_review` hands it back as `PublishedReview.location` (ADR-0044),
and `demo_notion.py` prints it — nowhere else. An editor looking at the brief in Notion sees
`waiting_human` and has no way to find the Doc.

`BriefBoard.report_status(brief_ref, *, run_id, status)` carries a status only. Two write paths were
possible, and the maintainer was asked (2026-09-17):

1. the core writes the link onto the brief through the Notion adapter, next to the statuses;
2. the core emits an event to an n8n webhook and an n8n Notion node writes status and link.

The maintainer chose (1): one writer to the brief, testable in CI, and n8n needs no write access to
Notion.

## Decision

### 1. An additive `BriefBoard` method

```python
def report_review_location(self, brief_ref: str, /, *, run_id: str, location: str) -> None:
    """Show where the Run's review can be found on its brief."""
```

Same rules as `report_status`: reporting the same location again is harmless, a failure never
changes the Run, and a technical failure is `BriefBoardError`. A blank `brief_ref` or `location` is
refused with `BriefBoardError` before anything is written. The location is the desk's opaque string;
the board does not interpret it. It is the location of the **latest published** review: a rework's
new review replaces it, and it stays after the Run completes (it is where the approved text was
reviewed).

`report_status` is unchanged. Adding a method to a Protocol is additive for callers; the two
implementations (`NotionBriefBoard`, `InMemoryBriefBoard`) and the tests' conformers gain it.

### 2. Notion: a `url` property, a seventh required variable

`NotionBriefBoard.report_review_location` writes `{"url": location}` into the property named by
**`OMEMO_NOTION_REVIEW_LINK_PROPERTY`**, which must be of Notion type `url` (clickable in the page).
As with `report_status`, a page not on the board or a missing / wrongly typed property is refused
**before** the `PATCH`. The variable is required like the other six (ADR-0040: no default property
names) — an operator upgrading must add it; a missing one is named by `notion_settings_from_env`.

### 3. `InMemoryBriefBoard` records it

`review_locations(brief_ref)` reads back `(run_id, location)` reports, oldest first; repeating the
last one is not recorded again; an unknown brief is refused with `BriefBoardError` (ADR-0025 §3).

### 4. `BriefStatusReporter.show_review_location(run, location)`

The reporter is already the brief's showcase, so it shows the link too:

- the location is reported when it differs from the one this reporter last showed **successfully**
  for the Run; otherwise nothing is sent;
- a `BriefBoardError` is logged at `WARNING` and kept as
  `FailedLocationReport(run_id, location, message)` in `failed_location_reports`; the location is
  not remembered as shown, so the next call tries again (a call happens once per invocation, not per
  commit, so there is no timeout-per-commit cost to guard against, unlike ADR-0041 §3);
- any other exception propagates; nothing is saved and the Run is never touched.

Not reporting an already-shown location matters beyond saving a request: n8n's Notion Trigger polls
the database by `last_edited_time` (Stage 11), so every write to the page triggers the core again. A
trigger that finds nothing new must write nothing.

### 5. `demo_notion.py`

After publishing a pending review, the demo shows its location on the brief and prints a refusal.

## Consequences

### Positive

- The brief in Notion links to the review Doc; the Stage 11 "links" line has a path.
- One writer to the brief; the n8n workflows stay free of Notion write credentials.

### Negative / Trade-offs

- A new required variable: an existing `.env` without `OMEMO_NOTION_REVIEW_LINK_PROPERTY` now fails
  closed at startup (named in the error). Accepted: an optional link would silently never appear.
- The link is not cleared when the Run fails or completes; the page's status says which.

## Alternatives considered

- **Core → n8n webhook → Notion node** — rejected by the maintainer (two writers, n8n write access).
- **A `location` field on `report_status`** — rejected: a status is reported on every commit, a
  location only after a publication; one call would have to carry an often-absent field.
- **An optional variable** — rejected (§2 trade-off).

## References

- `ROADMAP.md`: Этап 11; `ARCHITECTURE.md` §3.2, §12
- ADR-0023 §6, ADR-0025 §3, ADR-0040, ADR-0041, ADR-0043, ADR-0044
- `ADAPTER_SPEC.md` §5; `ADAPTER_ACCEPTANCE.md` §1, §6, §8, §9
