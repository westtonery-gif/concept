# REWORK_ROUTING_ACCEPTANCE.md — Acceptance criteria for resumable rework routing

> Acceptance for `REWORK_ROUTING_SPEC.md` / ADR-0032. Scenario ids are used by
> `tests/test_rework_routing.py`. All tests use deterministic fakes; no network or LLM.
>
> **Status:** Accepted. **Date:** 2026-09-15.

## 1. Route (RWR)

| ID | Scenario | Expected result |
|---|---|---|
| RWR-01 | QA flags v1; human requests changes; resume | only v1's producer is re-executed; v1 `SUPERSEDED`; v2 exists; `rework_count == 1` |
| RWR-02 | rework input | canonical JSON contains exact v1 identity/content, latest QA flags, human decision, human instructions and iteration |
| RWR-03 | v2 passes QA | QA targets v2, predecessor verdict/review do not carry over, Run follows the existing passing route |
| RWR-04 | v2 is risky and human requests changes again | one more producer Task creates linear v1 -> v2 -> v3; count is 2 |
| RWR-05 | latest review is `PENDING` or `APPROVED` (on a risk verdict) | resume does not start rework or call the executor |
| RWR-07 | latest review is `REJECTED` with a reason (ADR-0045 §1) | the same route as `CHANGES_REQUESTED`: one producer Task, v1 `SUPERSEDED` (never `REJECTED`), v2 `CANDIDATE`; input `human_decision` = `rejected`, `human_instructions` = the reason |
| RWR-06 | restored rework continues without a QA evaluator | successor remains `DRAFT` and Run stops at `WAITING_QA` |

## 2. Failure and bounds (RWF)

| ID | Scenario | Expected result |
|---|---|---|
| RWF-01 | rework Task fails | Run `FAILED`; v1 remains `CANDIDATE`; no successor |
| RWF-02 | rework Task succeeds without validated Output | Run `FAILED` with `REWORK_NO_OUTPUT_REASON`; v1 remains live |
| RWF-03 | ReworkPolicy exhausted | no Task/executor call; Run `FAILED` with `REWORK_LIMIT_REASON`; no successor |
| RWF-04 | rework plan does not match stored Tasks | `RunResumptionError` before status/count/children change |

## 3. Storage and resumption (RWS)

| ID | Scenario | Expected result |
|---|---|---|
| RWS-01 | crash after committing `WAITING_HUMAN -> RUNNING`, before opening Task | restart opens exactly one rework Task |
| RWS-02 | crash after committing rework Task `RUNNING` | restart retries the same Task on its exact stored input; attempt count exposes the retry |
| RWS-03 | crash after committing terminal Task + v2 | restart reuses Task/Output/version and proceeds; no duplicate call or Artifact |
| RWS-04 | resume an already completed rework route | no calls and no state changes |

## 4. Regression (RWG)

| ID | Scenario | Expected result |
|---|---|---|
| RWG-01 | QA risk before a human decision | unchanged: `WAITING_HUMAN` with a `PENDING` escalation review |
| RWG-02 | normal run without rework | existing ContentDirector and storage acceptance remains green |
| RWG-03 | persisted rework Run round-trip | existing snapshot/SQLite format restores the full version chain and feedback Task input |
