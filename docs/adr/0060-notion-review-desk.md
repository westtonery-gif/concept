# ADR-0060: A `ReviewDesk` on Notion — typed properties instead of a marker line, and its own database

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Lead Architect / Domain Architect
- **Serves:** `CLAUDE.md` queue task 19; unblocks Milestone M3 (16.3) and the clipping department's
  human gate (task 21, ADR-0059)

## Context

`GoogleDocsReviewDesk` (ADR-0043) cannot be used in the maintainer's environment: they cannot
create a Google service account, so `OMEMO_GOOGLE_*` are unset, `has_desk` is `False`, and Stage
12's `→ Google Docs →` leg is unproven. Milestone M3 has stayed open on it since 2026-09-19.

Queue task 19 held the alternative and required a choice between it and a Google service account.
The maintainer chose the **Notion desk** (2026-09-20).

Two things changed the weight of that choice since the task was written. ADR-0059 settled that an
episode's Run carries ~13 clip Artifacts, each needing its own human decision — so the desk is no
longer a one-review-at-a-time convenience for the factory, it is the clipping department's gate as
well, at roughly 390 reviews a month. And ADR-0055 §5 resolved the task's own open question about
the token: the integration writes, and always did.

The port is vendor-neutral and unchanged (`adapters/review_desk.py`, ADR-0023 §7):
`publish(ReviewPackage) -> location`, `fetch_decision(review_id) -> ReviewDecision | None`. A second
implementation is additive, exactly as `NotionBriefBoard` was.

## Decision

### 1. Its own database, not a page in the brief board

A review is its own record: it outlives the brief, it belongs to a Run and an Artifact rather than
to an editorial item, and at ~390 a month from the clipping department alone it would bury a brief
board that holds tens of rows. A dedicated database also lets its properties be exactly the
decision's, with nothing shared by coincidence.

The same integration serves both databases. Per ADR-0055 §5, the prerequisite is **content access**:
the `concept` integration must be granted the review database explicitly, or every lookup returns
nothing and is indistinguishable from "not decided yet".

### 2. The decision is typed properties, not a marker line — and that is a departure from ADR-0043

Task 19 suggested reusing ADR-0043 §2's grammar (`РЕШЕНИЕ:` / `ПРИЧИНА:` above a separator) on the
grounds that it is about text, not about Google. On inspection that is the wrong inheritance: the
marker line exists because **a Google Doc has no properties**. It is a workaround for a constraint
Notion does not have, and carrying it over would import the workaround's whole failure surface —
prose parsing, a damaged block, a marker missing or repeated or out of order, an unrecognised word
that has to be treated as "not decided" plus a `WARNING` because a typo is not a fault.

The Notion desk therefore reads:

- **`Решение`** — a `select` property with exactly three options, mapping to `ReviewStatus`:
  `Одобрено` → `APPROVED`, `Отклонено` → `REJECTED`, `Доработать` → `CHANGES_REQUESTED`. Unset →
  `None`, which is "still pending" and the only ambiguity left. An option outside the three is a
  misconfigured database, so it is a `ReviewDeskError`, not a silent `None`.
- **`Причина`** — a `rich_text` property carrying a rejection's reason or the requested changes;
  blank → `None`, matching `ReviewDecision`'s contract that a reason is `None` or non-blank.

A reviewer picks from a dropdown instead of typing a keyword. There is no parsing, no typo, and no
ambiguity between "wrote nothing" and "wrote something I could not read". The **Notion API can
create `select` properties** (it cannot create `status` ones — the operator log recorded that when
the brief board was set up), so the setup is scriptable.

ADR-0043 is not superseded: its grammar remains correct for a Doc, which has nowhere else to put a
decision.

### 3. `review_id` is a queryable property, and no id goes into a URL

`publish` writes the `review_id` into a `rich_text` property and finds a review again by
**querying the database with a filter on it**. ADR-0043 hashed the id into Drive `appProperties`
because a Drive lookup puts its query in the URL; a Notion database query is a `POST` with a JSON
body, so the id never enters a path or a query string and there is nothing to hash. Page ids are
still percent-encoded wherever they do appear in a path, as ADR-0040 requires.

Publishing the same `review_id` twice returns the same page rather than creating a second one — the
port's idempotence requirement (ADR-0023 §7), and the reason `publish` is also the retry
(ADR-0044 §4). Two pages carrying one `review_id`, or another package under a published review, are
`ReviewDeskError`.

### 4. A clip is reviewed by its file path, and the reviewer plays it themselves

`ReviewPackage.candidate` is an `ArtifactView` whose `content` is text (ADR-0006), which is all a
script needs. A clip is a video, and in v1 it is a local file (ADR-0053 §7). Notion cannot play a
file that exists only on the maintainer's machine.

So the review page carries the clip's **plan and its local path**, and the reviewer opens it in
whatever player they like before choosing from the dropdown. This is honest about what v1 is — a
single maintainer, a local file, a test — and it keeps the desk free of a storage dependency it
would otherwise need. When clips have to reach a reviewer who is not at that machine, that is an
upload adapter and its own decision, not a change to this desk.

### 5. The Notion HTTP plumbing is extracted **first**, as its own change

This is the third consumer of the same `urllib` + auth + property handling, after
`NotionBriefBoard` and `NotionEpisodeBoard`. ADR-0055 §3 named this exact moment: duplication was
deliberate at two, and *"when the third arrives, extract it … as its own behaviour-neutral refactor
… never mixed into the feature commit that revealed the need."*

The ordering is decided here: **extract before building the desk, not after.** Extracting first is
behaviour-neutral over two existing, tested consumers with the full gate as the proof; extracting
afterwards would mean writing the plumbing a third time and then removing it, with the desk's own
new behaviour in the same diff obscuring whether the refactor changed anything.

That refactor gets its own ADR and its own session. It touches `infrastructure/` only — no port, no
application code, no `Run`.

## Deferred

- **The extraction ADR** (§5), which is now the immediate next task.
- **Per-Artifact publication and decision-reading.** ADR-0059 §5 found `pending_review` returns
  `pending[-1]`; the clipping department needs per-Artifact siblings. That is application code and
  belongs to task 21.6, not to this adapter — the desk itself is already per-`review_id`.
- **Hosting clips for a remote reviewer** (§4).
- **Whether the review database needs a link back to the brief or episode.** Useful, not required by
  the port, and cheap to add once the first real batch shows what is missing.

## Consequences

### Positive

- M3's last structural gap closes without a Google account, and the clipping department's gate
  exists before it is needed rather than after.
- The decision path loses an entire class of failure: no prose parsing, so no damaged marker block,
  no unrecognised word, no typo treated as silence.
- The extraction lands where the rule said it would, on evidence rather than on anticipation.

### Negative / Trade-offs

- Two desks now exist with different decision mechanisms. A reader comparing them will see a marker
  line in one and a dropdown in the other; §2 is the reason and has to be read for it to make sense.
- A clip review depends on the reviewer being at the machine holding the file.
- One more Notion database for the operator to create, share with the integration and keep in sync
  with property names that the factory refuses to default.

## Alternatives considered

- **A Google service account after all.** Not available to the maintainer; that is what opened this
  question.
- **Reuse ADR-0043's marker grammar in Notion**, as task 19 suggested. Rejected per §2: it is a
  workaround for an absent constraint, and it would import its failure modes for nothing.
- **A page in the brief database.** Rejected per §1: ~390 clip reviews a month would bury a board
  holding tens of briefs, and a review is not an editorial item.
- **Extract the Notion plumbing after the desk works.** Rejected per §5: the refactor's proof is
  that behaviour did not change, and that proof is worthless in a diff that also adds behaviour.

## References

- `PROJECT.md` §12 (nothing ships without Approve); `ARCHITECTURE.md` §13
- ADR-0023 §7 (the `ReviewDesk` port), ADR-0025 (`InMemoryReviewDesk`), ADR-0040 (the Notion
  adapter's shape and required variables), ADR-0043 (the Google desk; its grammar and why it exists),
  ADR-0044 §4 (publish is the retry), ADR-0045 (decision fetch), ADR-0055 §3/§5 (the rule of three;
  the token writes, access is the prerequisite), ADR-0059 §5 (the single-review assumption in
  application code)
- `n8n/README.md` → Operator verification log (2026-09-18: the API cannot create `status`
  properties; 2026-09-19: the live pilot wrote back through the same token)
- `CLAUDE.md` queue tasks 16.3, 19, 21
