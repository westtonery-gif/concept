# ADR-0051: The ROADMAP Stage 12 (MVP) acceptance, and what Milestone M3 still needs

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** Lead Architect / Domain Architect
- **Closes:** ROADMAP Этап 12 as code (`CLAUDE.md` queue 16.1, 16.2); **not** Milestone M3 (16.3)

## Context

Stage 12 is the project's key milestone: the whole loop —
**Notion → Content Director → Agent → QA → Google Docs → Human Approval** — working as one system,
not as components that each pass their own acceptance. Its Definition of Done (ROADMAP.md, and
`PROJECT.md` §2):

1. one real brief goes through the whole pipeline to an artifact **approved by a human**;
2. any run is reproducible from its stored input and trace;
3. no artifact leaves without an explicit Approve;
4. every inter-agent message is schema-validated;
5. a failing step does not break the system: the `Run` is left in a managed state.

Stage 12 was estimated "Высокая / L", as if the loop still had to be assembled. It does not. The
acceptance chain built one stage at a time already runs it end to end: `S8A` (QA gate on the real
Director), `S9A` (brief intake from a board), `S10A` (review desk, decision, rework) and `S11A` (the
same path behind the real HTTP service, driven by requests rendered from the committed n8n
workflows). What none of them does is **read the DoD lines as the DoD states them** — as properties
of the assembled system rather than of the stage each was written for:

- `S11A-04` completes a Run through the sweep but never looks at the candidate's `ArtifactStatus`,
  so "an artifact **approved by a human**" is asserted only at `S10A`/`S8A`, below the trigger.
- No test states reproducibility as one property: that the stored row alone carries the brief that
  was produced, every inter-agent message, the verdict, the human's decision and the cost of every
  provider turn.
- Schema validation of inter-agent messages is proven at `M2A` (`INVALID` stops the plan), on a
  Director-level pass with no board, no desk and no trigger.
- `S9A-05` shows a crash during brief intake, but in a CLI process. Behind the service a failing job
  is a caught exception in the worker (ADR-0049), and `S11A-07` covers only a board refusal — not a
  process dying mid-intake, and not a trigger the service cannot even dispatch.

## Decision

### 1. Stage 12 is closed at the CI level by one acceptance that reads the DoD, not by re-assembly

`tests/test_stage12_acceptance.py` (`STAGE12_ACCEPTANCE.md`, prefix `S12A`) runs the **Stage 11
production path unchanged** — the real `ProductionService` on `127.0.0.1`, `BriefProduction` over
`build_run_index`, bundled Prompts for Rin, Leo and QA, Rin's Skill and Tool, real
`AnthropicLLMClient`s with the transport below the SDK scripted, the real `SqliteRunStore` under
`BriefStatusReporter`, an in-memory board and a desk playing a Google Doc, and every request rendered
from the committed n8n workflow files. Nothing is rebuilt and no production code changed: `S12A`
adds assertions, and the two helpers below, not a second wiring of the factory.

Each `S12A` test is named after the DoD line it answers, and every line is answered **on the full
loop, entered through the n8n trigger** — which is what "as a system" means here and why the
existing per-stage coverage does not already close the stage.

### 2. A crash is tested where Stage 12 is exposed: intake, and dispatching the trigger

Two failures neither `S11A` nor `SWR` covers, both entered through the trigger:

- the process dies mid-intake, after the `queued` commit and before the first Task. Behind the
  service that is a worker job raising: the service must answer the next request, the stored Run
  must be a managed `QUEUED` with no Task, and the next trigger must produce it **from the brief on
  the board** (ADR-0042's resume rule), not from an empty one;
- the service cannot dispatch the scheduled trigger at all, because listing the waiting Runs fails.
  The sweep answers `503`, queues nothing, calls no model and leaves every stored Run untouched; a
  later sweep, once the listing works, completes the Run.

To drive these, `S11A`'s `Factory` gains two optional keyword arguments, `dies_after_saves` (the
`_DyingStore` `S9A` already uses for the same purpose) and `index` (a `RunIndex` that can refuse).
Both default to today's behaviour, so `S11A` is unchanged — extending the previous stage's harness
is how `S9A`, `S10A` and `S11A` were each built.

### 3. Milestone M3 is **not** closed by this ADR — the pilot is an operator action

Stage 12's DoD says "**one real brief**". `S12A` scripts the transport below the Anthropic SDK, plays
Notion with an in-memory board and Google Docs with an in-memory desk, and renders what n8n would
send instead of running n8n. That is the right boundary for CI, and it is not the milestone.

Milestone M3 needs one brief through real Notion, real Google Docs, real Anthropic and a real n8n
trigger, to a human-approved artifact — four external accounts this environment does not have. It
stays the operator's check, the same role `demo_factory.py` plays for live provider calls,
`n8n/README.md` for a live Notion Trigger and ADR-0043's folder setup for a live Doc. A scripted
test is **not** allowed to stand in for it: calling the milestone done because CI is green is exactly
the "components work separately but not as a system" risk the stage exists to remove.

So: ROADMAP Stage 12 is complete **as code**; Milestone M3 is **open**, blocked only on the live
pilot, and `CLAUDE.md`'s queue says so.

## Consequences

- Every Stage 12 DoD line has a test that asserts it as the DoD words it, on the assembled loop.
- Stage 13 may start on the code; the M3 banner may not be claimed until the pilot runs.
- `S12A` depends on `S11A`'s harness (and through it on `S8A`–`S10A`); a change there is felt in four
  acceptances at once. That is the same coupling the chain already had, and it is what keeps the
  stages proving one production path instead of four look-alikes.

## References

- `ROADMAP.md`: Этап 12; `PROJECT.md` §2, §12
- ADR-0033 (M2), ADR-0039, ADR-0042, ADR-0046, ADR-0049, ADR-0050
- `STAGE12_ACCEPTANCE.md`; `tests/test_stage12_acceptance.py`
