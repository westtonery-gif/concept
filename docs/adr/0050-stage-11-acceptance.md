# ADR-0050: The ROADMAP Stage 11 (n8n) acceptance

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect
- **Closes:** ROADMAP Этап 11 (`CLAUDE.md` queue 15.4)

## Context

Stage 11 was built in three subtasks: the review link on the brief (ADR-0047), one brief invocation
as application code plus listing Runs by status (ADR-0048), and the HTTP production service with the
committed n8n workflows (ADR-0049). Its Definition of Done (ROADMAP.md):

1. creating a brief in Notion automatically starts a `Run` through n8n;
2. business logic stays in the Content Director; n8n does only transport and triggers.

`SVC` tests the service with recording doubles and `N8N` reads the workflow files. Neither shows the
two together on the production path, and neither shows the property ADR-0049 was built around: a
polling trigger that fires again on the core's own writes must settle, not loop.

## Decision

`tests/test_stage11_acceptance.py` (`STAGE11_ACCEPTANCE.md`, prefix `S11A`) runs the **real service**
(`composition.build_production_service`) on `127.0.0.1` over S10A's production path — the bundled
Prompts for Rin, Leo and QA, Rin's Skill and Tool, real `AnthropicLLMClient`s with the transport below
the SDK scripted, the real `SqliteRunStore` under `BriefStatusReporter`, an in-memory board and a desk
playing a Google Doc — assembled as `BriefProduction` with `build_run_index`.

n8n itself is not run. Instead every request is **rendered from the committed workflow file**: its
HTTP Request node's method, route and body parameters, with `={{ $json.id }}` evaluated against an
item shaped like the Notion Trigger's simplified output, and the Header Auth credential replaced by
the token. A workflow edit that changes what n8n sends therefore changes what the acceptance sends;
a body expression the renderer does not know fails the test instead of being guessed.

It covers: a ready page produces a Run with statuses and the review link shown; an unready or unknown
page does nothing; repeated triggers after the core's own writes cause no model call and no board
write; an approval typed on the desk is picked up by the sweep; changes requested are reworked by the
sweep and the new link shown; a wrong credential is refused; a job that fails does not stop the
worker; a restarted service's sweep finds a Run the previous process left waiting.

The node parameter names were taken from the `n8n-nodes-base` sources, not guessed, and the files
were then checked once against a real n8n 2.39.7 (a local install, not part of CI — several
gigabytes):

- `n8n import:workflow` of both files and `export:workflow` back: types, versions, parameters,
  credentials by name and connections survive unchanged. This found that CLI import requires a
  top-level workflow `id`; both files now carry one (`N8N-01`).
- The sweep workflow, with only its port changed and its interval shortened to 10 s, was activated
  under `n8n start` with a Header Auth credential named `Concept factory service`; n8n called a live
  `factory_service.py` (fake model bindings), which logged `POST /v1/reviews/sweep` → `202`. The
  credential resolves by name and the bearer token is accepted.

A live Notion Trigger (needs a real Notion integration and database) stays the operator's check
(`n8n/README.md`).

## Consequences

- Both DoD lines are covered on the production path, through the HTTP boundary n8n uses.
- The acceptance depends on the workflow files' shape; the `N8N` tests pin that shape separately.

## References

- `ROADMAP.md`: Этап 11
- ADR-0046, ADR-0047, ADR-0048, ADR-0049
- `STAGE11_ACCEPTANCE.md`; `tests/test_stage11_acceptance.py`
