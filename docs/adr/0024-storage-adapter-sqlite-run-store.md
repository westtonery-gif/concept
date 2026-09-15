# ADR-0024: Storage Adapter — Run restoration realised, an embedded SQLite `RunStore`

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 6 asks that the "Storage Adapter really persists `Run`", so that state survives a
restart. The session queue calls this Stage 6b (task 7.4). Three accepted documents already
constrain it:

- `ADR-0023` §5 fixes the contract, `RunStore.save(run)` / `load(run_id) -> Run | None`, and the
  shared adapter rules (§4): an implementation lives in `infrastructure/`, raises its own technical
  error, writes idempotently, and lets domain errors pass through unmasked.
- `ADR-0015` admits a *restored Run*, the same aggregate brought into existence at a preserved
  state, and defers its form to the next level.
- `RUN_RESTORE_SPEC.md` 1.0 (2026-07-01) is that level: `RunSnapshot` + `Run.restore` with
  verify/reject. It was never implemented.

Revisiting the spec before implementing it turned up four gaps:

1. **It predates `ADR-0018`/`ADR-0020`.** The snapshot has no Evaluations, no Analytics Records and
   no `evaluation_seq`/`analytics_seq` counters. A restored Run would lose its QA verdicts, and with
   them the fail-closed gate.
2. **No observation.** §6 says "`RunStore.save(run)` наблюдает Run и получает `RunSnapshot`", but
   the policies and id counters are private and no view carries them. The store cannot take a
   snapshot through the public API.
3. **The counter check is too weak.** `task_seq ≥ |tasks|` admits a snapshot whose only Task is
   `…-task-3` with `task_seq = 2`. The next `open_task` then generates `…-task-3` and overwrites that
   child, which is exactly the duplication `ADR-0015` I3 forbids.
4. **RST-03 expects a guard that does not exist.** It expects `WAITING_HUMAN → COMPLETED` "без
   Approve" to raise `InvalidTransitionError`. The Approve gate sits on the Artifact
   (`ArtifactNotApprovedError`, `ADR-0007` §6); `Run.transition` knows nothing about reviews.

Constraints on the store itself:
- MVP persistence is "простое прослеживаемое хранилище прогонов (например, файловое/встраиваемая
  БД); промышленная СУБД вводится по мере роста" (`PROJECT.md` §5).
- Every transition is committed atomically, and the aggregate is saved as a whole
  (`ARCHITECTURE.md` §10, `ADR-0023` §5).
- No new runtime dependency without need (`CONTRIBUTING.md` "Scope discipline").

## Decision

### 1. The spec is amended first (`RUN_RESTORE_SPEC.md` 1.1)
- The snapshot gains `evaluations: tuple[EvaluationView, ...]` (the view is complete),
  `analytics_records: tuple[AnalyticsRecord, ...]` (frozen, exposed directly like `Output`),
  `evaluation_seq` and `analytics_seq`. Every child collection is a tuple.
- **Observation:** `Run.snapshot` is a read-only property returning the `RunSnapshot`, like the
  existing views. `Run.restore(run.snapshot)` gives back an indistinguishable Run.
- **Verify/reject** checks every invariant that the creation path already enforces, and nothing
  more (§4.3 of the spec):
  - the status is a `RunStatus`, and the fixed input is non-blank;
  - every child **and every journal event** carries this `run_id`;
  - the id of each kind of child is unique, including Output ids;
  - an Output belongs to its own Task, and only to a `SUCCEEDED` one;
  - Output → Artifact is 1:1;
  - `supersedes_ref`, reviews and evaluations point at owned Artifacts;
  - an Analytics Record names an owned Task and that Task's `agent_ref`;
  - `rework_count` and each `attempt_count` are within their policies;
  - a `COMPLETED` Run owns no non-terminal Task;
  - each counter is at least the number of its children **and** at least every sequence number
    already used in an id of the form `{run_id}-{kind}-{n}`.
- It adds no new domain rule: each check restates a `RUN_SPEC.md` §5 invariant as the existing
  operations enforce it.
- `RUN_RESTORE_ACCEPTANCE.md` 1.1 corrects RST-03 to the real Artifact gate and adds RST-15…21.

### 2. Additive domain API
| Addition | Module | Visibility |
|---|---|---|
| `RunSnapshot` (frozen value) | `domain/run.py` | public |
| `Run.snapshot` (read-only property) | `domain/run.py` | public observation |
| `Run.restore(snapshot)` (classmethod) | `domain/run.py` | public, the second factory |
| `RunRestorationError(RunDomainError)` | `domain/run.py` | public |
| `TaskSnapshot` (frozen value) | `domain/task.py` | public type; leaves the aggregate only inside a `RunSnapshot` |
| `Task.restore` / `Task.snapshot`, `Artifact.restore`, `HumanReview.restore`, `Evaluation.restore` | child modules | internal, called only by `Run.restore` / `Run.snapshot`, like the child constructors |

- Nothing existing changes: `create`, the transition tables, every signature and every event stay
  as they are.
- `restore` makes no transition and emits no event. The journal is taken verbatim.
- The child modules still do not import `run`.

### 3. The store: `SqliteRunStore` (`infrastructure/sqlite_run_store.py`)
- **Embedded SQLite**, through the stdlib `sqlite3` module: no new dependency. One table,
  `runs(run_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)`, with **one row per Run**.
- The row holds the whole snapshot as one JSON document, `{"format": 1, "run": {...}}`, produced by
  `infrastructure/run_snapshot_codec.py`.
- **`save`** encodes first, then runs one `INSERT OR REPLACE` in one transaction. That makes it
  atomic: a failure rolls back and leaves the previous truth whole. An unchanged Run encodes to the
  identical document, so repeating a save changes nothing.
- **`load`** returns `None` when there is no row; otherwise it decodes the row and calls
  `Run.restore`.
- Each call opens and closes its own connection. `timeout` (default 5 s) bounds the wait for
  another connection's lock.

### 4. Error mapping
| Situation | Raised |
|---|---|
| The database cannot be opened, read or written (including a lock timeout) | `RunStoreError` |
| The row is not a well-formed document of a known format: bad JSON; unknown or `bool` format version; a missing, extra or ill-typed field; an unknown enum value or event type | `RunStoreError` (wrapping `SnapshotFormatError`) |
| The row under `run_id` holds another Run | `RunStoreError` |
| `save` meets a journal event class the codec does not know (a bug) | `RunStoreError`, before anything is written |
| A well-formed snapshot the Run refuses | `RunRestorationError`, unmasked |
| A value the domain refuses while being rebuilt (for example a negative `Cost`) | that `DomainError`, unmasked |

### 5. The codec follows the snapshot's type hints
- The stored form is derived from the snapshot types' own hints:
  - an `Enum` is stored as its value;
  - a `Decimal` as a string (costs stay exact);
  - a `datetime` as ISO 8601 with its offset;
  - a frozen dataclass as an object of its fields.
- Journal events, the one polymorphic field, carry their class name under `"type"` and are read
  back through the `EVENT_TYPES` registry. A test pins that registry to every journal event class
  defined in the domain.
- A field added to a snapshot type is therefore written and read with no codec change. A document
  stored *before* that change lacks the field and is refused (`RunStoreError`), never guessed.
  Layout changes bump `FORMAT_VERSION`; migrations are deferred.

### 6. Not wired yet
No entrypoint and no `ContentDirector` saves a Run, and the Composition Root does not build a
store. That wiring is when and where to save ("each transition is committed atomically",
`ARCHITECTURE.md` §10). It belongs to Stage 7, where the orchestrator first runs a real agent end
to end (`ADR-0023` "Deferred").

### 7. Stage 6 DoD after this ADR
| DoD item | Status |
|---|---|
| Contracts of the five adapters; the core calls no external API directly | done (`ADR-0023`) |
| LLM swapped through configuration | done (`ADR-0016`) |
| Storage really persists `Run` | **done**: `SqliteRunStore`, tested against a real database file |
| The other contracts tested against fakes | 6c: `BriefBoard` / `ReviewDesk` / `AnalyticsSink` stubs |

## Deferred
- **Wiring:** saving after each transition, the database location, and its configuration variable
  (Stage 7).
- **Format migrations:** reading a `FORMAT_VERSION` older than the current one. Nothing is stored in
  production yet.
- **Concurrent writers:** an optimistic version column or row locking. `ADR-0015` excluded
  concurrent continuation, so the last `save` wins.
- **Listing stored Runs**, for example "all unfinished Runs" after a restart (`ADR-0023` "Deferred").
- **An industrial DBMS** (`PROJECT.md` §5): a new implementation of the same `RunStore` Protocol.
- **Durable availability of the definition versions** a Run references: the required dependency
  named by `ADR-0015` §2.
- **Deeper admission checks:** for example, a status consistent with the journal, or
  `WAITING_HUMAN` requiring a candidate. These edge toward replay. Add them by spec amendment when a
  reader needs them.

## Consequences

### Positive
- The Stage 6 Storage DoD holds: a Run survives a restart with its children, QA verdicts, analytics
  records, policies, counters and journal. It continues without id collisions.
- The restored Run is the same aggregate under the unchanged transition contract. The QA gate and
  the Approve gate survive restoration by construction.
- The spec gaps (missing children, no observation, weak counter check, a phantom guard in RST-03)
  are closed in the spec before the code.
- No new dependency. Swapping in an industrial DBMS is one new `infrastructure` module.

### Negative / Trade-offs
- Storing the aggregate as one document means no SQL querying inside a Run. That is acceptable
  while the aggregate is always loaded whole.
- The stored layout follows the domain types. Renaming or adding a field makes older documents
  unreadable until a migration exists (Deferred), and the failure is loud (`RunStoreError`), not
  silent.
- `Run.restore` holds a list of structural checks that has to grow with each new child or
  invariant, via a spec amendment.
- The Run now has two public ways to come into existence (`ADR-0015` already accepted that).

## Alternatives considered
- **One JSON file per Run, replaced atomically.**
  - A file name must encode an opaque `run_id` (separators, reserved names), and the default
    macOS/Windows filesystems are case-insensitive, so `Run-1` and `run-1` would share one file.
  - Replacing a file that another handle holds open is fragile on Windows.
  - SQLite gives the transaction and the key lookup for free.
- **Normalised tables per child.** Every additive child (the `ADR-0018`/`0020` pattern) would become
  a schema migration, and the aggregate is always loaded whole anyway.
- **`pickle`.** It runs code on load, couples storage to private fields, and is unreadable.
- **A hand-written codec per type.** It is explicit, but every new field then touches two places.
  The hint-driven codec plus the registry test catches the same drift.
- **Validating in `RunSnapshot.__post_init__`.** A snapshot is data; admitting it is the Run's
  decision (`RUN_SPEC.md` §4a), and `Run.snapshot` never produces an invalid one.
- **A `RunStore` that speaks `RunSnapshot`.** Rejected by `ADR-0023` §5.

## References
- `PROJECT.md`: §4.11, §5, §10
- `ARCHITECTURE.md`: §9, §10
- `ROADMAP.md`: Stage 6 (and Stage 7 for the wiring)
- `RUN_SPEC.md`: §4a, §5
- `ADR-0004`, `ADR-0007`, `ADR-0015`, `ADR-0018`, `ADR-0019`, `ADR-0020`, `ADR-0023`
- `RUN_RESTORE_SPEC.md` 1.1, `RUN_RESTORE_ACCEPTANCE.md` 1.1, `ADAPTER_SPEC.md` §4,
  `ADAPTER_ACCEPTANCE.md` §5
