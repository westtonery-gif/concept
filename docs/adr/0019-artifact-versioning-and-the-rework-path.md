# ADR-0019: Artifact versioning (`SUPERSEDED`) — the rework path

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

A fixed `Artifact` is an **immutable version**: the domain changes content only "через порождение
новой версии Artifact агентом доработки", and an edit is "новая версия (`Superseded`)"
(`DOMAIN_MODEL.md` §2.12, §6). Creating that new version is one of the operations reserved to the
`Run` root (§9.1). Its documented transition is "любой рабочий → `Superseded` (при новой версии)",
with `Published`/`Rejected`/`Superseded` terminal.

None of that is realised yet. `ArtifactStatus.SUPERSEDED` exists as vocabulary but
`_ALLOWED_ARTIFACT_TRANSITIONS` gives it **no edges**, `version` is hardcoded to `1`, and the
optional documented attribute "ссылка на предыдущую версию" has no representation. So the system
can produce a candidate, evaluate it and have it rejected — but it **cannot act on that**:

- ADR-0018 made the QA gate fail closed. A `FLAGGED`/`FAILED` verdict stops the Run at
  `WAITING_HUMAN` with an escalation review, and a human `Approve` cannot override it. The only way
  out is "a new, passing evaluation" — which requires a new version. ADR-0018 §"Deferred" names
  exactly this ("rework routing on a risk verdict … needs Artifact versioning").
- ADR-0007 left `CHANGES_REQUESTED` rework routing deferred for the same reason.
- `PROJECT.md` §12 and `ROADMAP.md` Stage 8 both route risk to "доработка/эскалация"; rework is a
  first-class outcome, not an error path.

This ADR closes that gap in the domain. It mirrors ADR-0004…0007/0018: additive, Open/Closed
(`PROJECT.md` §4.11), no infrastructure, and the only change to existing behaviour is the
**intended** extension of the Artifact lifecycle (as ADR-0007 extended it before).

## Decision

### 1. Placement
No new module and no new entity: a version **is** an `Artifact`. The change is confined to
`domain.artifact` (one new attribute, the extended transition table, one new error) and to `Run`'s
additive public API. `Artifact` keeps its dependency-free position (opaque `str` references,
ADR-0003 §3): the predecessor reference is a plain `str`, so no cycle and no new coupling.

### 2. A new version is a new Artifact, not a mutated one
Superseding never edits the old Artifact's content — content, `kind`, provenance and `version` stay
write-once (ADR-0006 §2). The successor is a **separate `Artifact` instance** with its own id,
`version` = predecessor + 1, its own `content` (from the reworked Output) and a new optional
attribute `supersedes_ref: str | None` — the documented "ссылка на предыдущую версию" (§2.12),
`None` for a first version. It is exposed on `ArtifactView`. The pointer is **backwards only**: the
predecessor is immutable, so it gains no "superseded_by" field; the chain is read by following
`supersedes_ref`.

Each version keeps its own Output provenance, so **Output → Artifact stays 1:1** (ADR-0006 §5) and
the invariant "каждый Artifact порождён шагом" holds for every version: a rework is a real
(re-)execution producing a real new Output, not a content edit invented by the root.

### 3. Lifecycle extension — any *working* state → `SUPERSEDED`
The structural table in `artifact.py` gains three edges (`DOMAIN_MODEL.md` §2.12):

| From | To |
|------|-----|
| `DRAFT` | `CANDIDATE`, **`SUPERSEDED`** |
| `CANDIDATE` | `APPROVED`, `REJECTED`, **`SUPERSEDED`** |
| `APPROVED` | `PUBLISHED`, **`SUPERSEDED`** |
| `REJECTED` / `SUPERSEDED` / `PUBLISHED` | — (terminal) |

`REJECTED`, `PUBLISHED` and `SUPERSEDED` are terminal, so they cannot be superseded: a rejected or
published version is closed, and an already superseded one has a successor — which is also what
keeps the version chain **linear** (a second `create_artifact_version` on the same predecessor is
refused by the table itself, with no extra guard).

### 4. Run's additive public API — `create_artifact_version`
`create_artifact_version(previous_artifact_id, output_id, *, by, kind=None) -> ArtifactId`, beside
`create_artifact` (which is **unchanged**, as are all other `Run` signatures):

- Content Director only (`UnauthorizedActorError`), like every other Artifact operation.
- The predecessor must be owned by this Run (`KeyError`) and in a working state, else
  `InvalidArtifactTransitionError`.
- The Output must be recorded in this Run (`KeyError`) and unused (`DuplicateArtifactError`).
- Supersession and creation are **one operation**: every check runs before any state change, so a
  refused call supersedes nothing, and a Run can never hold a superseded version with no successor.
- `kind` defaults to the predecessor's (the same content unit at a later stage) and may be
  overridden for the rework (e.g. `"draft"` → `"edited"`).

**`SUPERSEDED` is not reachable through `transition_artifact`**: a bare status change would produce
exactly the orphan state the invariant forbids, so the root rejects it with
`ArtifactSupersessionError`. This is the ADR-0007 pattern — structural edges in `artifact.py`, the
cross-entity rule in the root, the entity unaware of its successor.

### 5. Events
No new event type: `DOMAIN_MODEL.md` §10 documents no supersession event, and inventing vocabulary
the model does not have is exactly what ADR-0006/0018 declined to do (`DRAFT → CANDIDATE` and
`open_evaluation` emit nothing for the same reason). The new version emits the documented
`ArtifactCreated`, extended **additively** with `version: int = 1` and
`supersedes_ref: str | None = None`, so the Run log stays a complete audit trail of what replaced
what, and the event is unchanged for every existing caller.

### 6. A new version starts a fresh QA / Human Review lifecycle
By construction, not by extra code: the successor is a new Artifact with a **new id**, and both
gates key on the Artifact id. The predecessor's `APPROVED` Human Review and its `PASSED` Evaluation
target the old id, so `transition_artifact(v2, APPROVED)` needs its own approving review
(`ArtifactNotApprovedError`) **and** its own `PASSED` latest evaluation (`ArtifactQaNotPassedError`,
ADR-0018 §5). Rework therefore cannot launder a risk verdict, and "re-evaluation is a new
Evaluation" (§2.13) is satisfied without touching `Evaluation`.

This makes the ADR-0018 loop closeable end to end in the domain: risk verdict on v1 → escalation →
new version v2 → new evaluation → `PASSED` → approve → publish.

### 7. Errors
One new error in `domain.artifact`: `ArtifactSupersessionError(ArtifactDomainError)` — rooted at the
shared `DomainError` (ADR-0017) — raised when `SUPERSEDED` is requested outside
`create_artifact_version`. Everything else reuses the existing errors.

## Deferred
- **Orchestration of rework**: `ContentDirector` routing a risk verdict / `CHANGES_REQUESTED` into a
  re-execution that produces the new version (and the `WAITING_QA`/`WAITING_HUMAN → RUNNING` rework
  re-entry it implies). That needs the rework agent and the Stage 7/8 orchestrator; the domain path
  it requires exists as of this ADR. `Run.transition`'s state machine and `ReworkPolicy` are
  untouched here.
- Superseding a `REJECTED` version (rework after an outright `Reject` starts a fresh first version;
  a rejected version is terminal per §2.12) and any cross-version version numbering beyond
  "predecessor + 1".
- Output → Artifact 1:N, flags/remarks on the Artifact itself (still ADR-0006 deferrals).
- A separate `ARTIFACT_SPEC.md` / `ARTIFACT_ACCEPTANCE.md`: Artifact is specified by
  ADR-0006/0007 + `DOMAIN_MODEL.md` §2.12 and has never had its own pair; this ADR adds one
  operation and three edges to that existing contract rather than a new aggregate (contrast
  ADR-0018, where a **new entity** justified `EVALUATION_SPEC.md`/`EVALUATION_ACCEPTANCE.md`).
  Behaviour is pinned by `tests/test_artifact_versioning.py`.

## Consequences

### Positive
- "Зафиксированная версия Artifact неизменяема; правка = новая версия" becomes real and enforced by
  the root; `SUPERSEDED` stops being dead vocabulary and `version` finally means something.
- The rework loop deferred by ADR-0007 and ADR-0018 is unblocked domain-side, with the fail-closed
  QA gate preserved across versions (no inherited approval, no inherited verdict).
- Full audit trail of the version chain in the existing Run log, with no new event vocabulary.
- `artifact.py`'s module docstring, stale since ADR-0007, now describes the actual lifecycle.

### Negative / Trade-offs
- The Artifact transition table changes, so the test that enumerates structurally forbidden edges
  gains `SUPERSEDED` as an allowed-but-root-gated target (the same exclusion `CANDIDATE → APPROVED`
  already has) — an intended change, as in ADR-0007/0018.
- `Run`'s public API grows by one method (additive); `ArtifactView` and `ArtifactCreated` grow by two
  fields with defaults.
- A Run's `artifacts` now includes superseded versions; readers that want "the live artifact" must
  filter by status (there is deliberately no "current version" accessor yet — no caller needs one).

## Alternatives considered
- **Mutate the Artifact in place (bump `version`, replace `content`)** — rejected: contradicts
  "зафиксированный Artifact трактуется как неизменяемая версия" (§2.12) and ADR-0006 §2's write-once
  content, and would destroy the audit trail of what QA actually judged.
- **Allow `SUPERSEDED` through `transition_artifact`** — rejected: it would let a Run supersede its
  only content unit with nothing replacing it; "при новой версии" is part of the transition, so the
  successor must be created in the same operation.
- **A dedicated `ArtifactSuperseded` event** — rejected: `DOMAIN_MODEL.md` §10 documents no such
  event; the supersession is fully traceable through `ArtifactCreated`'s `supersedes_ref`
  (cf. ADR-0006 §8 for `DRAFT → CANDIDATE`).
- **Reuse `create_artifact` with an optional `supersedes=` argument** — rejected: it would change an
  existing signature's meaning (extend by adding, `PROJECT.md` §4.11) and make an unrelated,
  frequently used call carry a rework concern.
- **Version without a new Output (copy the predecessor's content and edit it)** — rejected: breaks
  "каждый Artifact порождён шагом" and Output → Artifact 1:1; rework is a re-execution.
- **A `previous_version` object reference instead of an id** — rejected: `domain.artifact` keeps all
  references opaque (`str`, ADR-0003 §3), and object graphs inside the aggregate invite cycles.
- **Model versions as an `ArtifactVersion` value object inside one Artifact** — rejected: the
  documented state machine belongs to the Artifact itself, and QA/Human Review target an Artifact
  id — a version must be addressable as one.

## References
- `DOMAIN_MODEL.md`: §2.12 (Artifact — «Изменяет», «Состояние», «Переходы», optional "ссылка на
  предыдущую версию"), §6 (Artifact invariants), §9.1 (new version only through Run), §10
  (`ArtifactCreated`)
- `PROJECT.md`: §4 п.11 (Open/Closed), §10, §12, §17
- `ARCHITECTURE.md`: §3.9, §13; `ROADMAP.md`: Stage 8 («риск → доработка/эскалация»)
- ADR-0006 (Artifact contract), ADR-0007 (publication path — the pattern this mirrors), ADR-0017
  (`DomainError`), ADR-0018 (QA gate — the deferral this closes)
