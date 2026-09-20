# ADR-0055: The `EpisodeBoard` port and its Notion implementation — where an episode ready to clip comes from

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Lead Architect / Domain Architect
- **Serves:** `CLAUDE.md` queue task 21.4 (the clipping department, ADR-0053)

## Context

ADR-0053 §1 put the clipping department next and recorded the maintainer's choice that the "episode
ready to clip" board is a **separate** database, not the brief board. The working note's third
design point, and `n8n/README.md`'s own warning, fix the rule it has to obey: *"Readiness is the
board's rule … an IF node here would be a second copy of it."* Whichever episode is ready is decided
outside the core, never re-decided inside an agent's reasoning.

`BriefBoard` (ADR-0023 §6, ADR-0040, ADR-0041, ADR-0047) is the proven shape for exactly this:
`fetch_brief` hands over a brief that is ready and answers `None` for everything else, and
`report_status` shows the Run's progress back on the page. `NotionBriefBoard` proves the HTTP
client, the auth, the readiness property and the block handling against this very workspace.

The task carried one open question from ADR-0053's Deferred list: **whether the existing Notion
integration may write**, since `n8n/README.md` names a credential `Concept Notion (read)` and task
19 lists the same doubt. Reading the operator log settles it — see §5. It is not what it looked
like.

## Decision

### 1. A new port, `EpisodeBoard`, named by its role

`adapters/episode_board.py`, a `Protocol` beside `BriefBoard`, with its own technical
`EpisodeBoardError` (not a `DomainError`), mirroring ADR-0023's contract style:

```
fetch_episode(episode_ref, /) -> IncomingEpisode | None
report_status(episode_ref, /, *, run_id: str, status: RunStatus) -> None
```

`None` covers an unknown, an archived and a not-yet-ready episode alike — the core then starts no
Run, exactly as a brief that is not ready starts none. Reporting the same status twice is harmless,
and a failed report never changes the Run (ADR-0041's rule, inherited deliberately).

It is a **second port, not an extension of `BriefBoard`**. The two answer different questions and
carry different payloads; widening `BriefBoard` with episode methods would make every existing
implementation, including `InMemoryBriefBoard`, implement something it has no business knowing.

### 2. `IncomingEpisode` carries editorial intent, not the video

```
IncomingEpisode(episode_ref: str, source_ref: str, mode: ClipMode)
```

all non-blank, validated at construction like `IncomingBrief`:

- **`episode_ref`** — the board's opaque reference, which becomes the Run's brief reference the way
  `brief_ref` does today.
- **`source_ref`** — an opaque handle for the video, whatever the editor typed. The board is
  editorial state, not storage: it says *which* episode, never *where the bytes are*. Resolving
  `source_ref` to something readable is the episode-source port's job (ADR-0053 §7), whose first
  implementation reads a local file. This keeps the board unchanged when the video later moves to
  remote storage.
- **`mode`** — `ClipMode`, a closed enum: `SEMANTIC`, `CHUNK`, `BOTH`. ADR-0053 §5 established that
  the department cuts both ways, and which way a given episode is cut is an **editorial** decision,
  not something for an agent to infer. Putting it on the board lets the maintainer choose per
  episode with no code change. *This is the one reversible call in this ADR that was decided rather
  than asked* — the alternative, always running both modes, is a one-line change to the Workflow if
  the property turns out to be noise.

The port deliberately carries **no** clip length, count or platform. Those are production
parameters, and the moment they live on a board every editor has to fill them in for every episode.
They belong in configuration until something proves they vary per episode.

### 3. `NotionEpisodeBoard` is a separate module, and the plumbing is duplicated on purpose

`infrastructure/notion_episode_board.py`, stdlib `urllib`, `Notion-Version: 2022-06-28` pinned —
the same choices ADR-0040 made and for the same reasons (a handful of endpoints; `notion-client`
would drag in `httpx`). Readiness is a `status`/`select` property holding a configured value; the
`_READY_TYPES = ("status", "select")` pair matters, because the operator log records that **the
Notion API cannot create `status` properties at all** — only `select`. Statuses are written back
into two `rich_text` properties, so a repeat is harmless.

The HTTP request helper, the auth header, the page fetch and the property reader **are duplicated
from `NotionBriefBoard` rather than extracted into a shared Notion client.** This is the repo's own
rule, not laziness: *"Do NOT extract shared base classes prematurely (rule of three)"*
(`CLAUDE.md` Conventions, `PROJECT.md` §4 п.11). Two copies is the point at which extraction is
still premature.

**When the third arrives, extract it.** The third is already foreseeable: task 19's Notion
`ReviewDesk` is the same plumbing a third time. At that point the extraction is justified and must
land as its own behaviour-neutral refactor with its own ADR — never mixed into the feature commit
that revealed the need, the way ADR-0022 kept `ToolVersion`'s third copy out of its own change.

### 4. Configuration is its own variable set, with no defaults

`OMEMO_EPISODE_NOTION_TOKEN`, `_DATABASE_ID`, `_READY_PROPERTY`, `_READY_VALUE`, `_MODE_PROPERTY`,
`_SOURCE_PROPERTY`, `_RUN_STATUS_PROPERTY`, `_RUN_ID_PROPERTY` — **all required**, every missing one
named, the token never appearing in `repr` or a message. No default property names, exactly as
ADR-0040 decided: a default name silently binds the core to someone else's column.

A separate token variable does not mean a separate integration — it may hold the same secret. It
means the two boards can be moved apart later without a migration.

### 5. The "read-only token" is a misreading, and the real prerequisite is different

`n8n/README.md` names an n8n credential `Concept Notion (read)`. That name describes **n8n's** use
of it — n8n only polls the trigger — and is not a capability of the Notion integration behind it.
The operator log settles what the integration can actually do: on 2026-09-18 four properties were
**created** on the brief database through the Notion API with that token, and on 2026-09-19 the live
pilot **wrote** `Run status = waiting_human` back onto a page through `report_status` (ADR-0041).
The integration writes. It always did.

**The real prerequisite is access, not permission:** Notion grants an internal integration content
access per page or database, so the `concept` integration must be granted access to the **new**
episode database before `fetch_episode` can see anything. A database it cannot see is
indistinguishable from an empty one, which is why this is written down here rather than discovered
as a silent `None`.

This also resolves the identical doubt listed in queue task 19 for the Notion `ReviewDesk`. Noted
there, not acted on here.

### 6. What this ADR does not decide

- **Run granularity.** `report_status(episode_ref, run_id, status)` presumes **one Run per
  episode**, whose clips are its Artifacts — the shape that matches today's `Run`, where a brief
  yields a candidate through successive steps. ADR-0053 §5 also says every clip gets its own QA
  verdict and its own human gate, and `ContentDirector` today evaluates the **final step's**
  Artifact (ADR-0018). Those two do not obviously fit, and reconciling them is the planner/Workflow
  ADR's job (task 21.6), not this one. **If it turns out to be one Run per clip, this port's
  write-back needs revisiting** — flagged here so it is not discovered by surprise.
- **The trigger.** A second n8n Notion Trigger and a service route (`POST /v1/episodes`) mirror
  ADR-0049 and belong with the implementation. Two of that ADR's findings apply unchanged and should
  not be rediscovered: the Notion Trigger polls `last_edited_time`, so **a core write to the page
  re-triggers it** and a trigger with nothing to do must write nothing; and queue task 18's hole — a
  refused or undelivered trigger is silently lost — will be open on this board exactly as it is on
  the brief board.
- **Reporting clip locations back** onto the episode page, the way ADR-0047 shows a review link.
  There is nothing to link to until clips are rendered and a desk exists.
- **The in-memory stub** (`InMemoryEpisodeBoard`, ADR-0025's shape, with a control side outside the
  Protocol) and the acceptance rows land with the implementation, in `ADAPTER_SPEC.md` /
  `ADAPTER_ACCEPTANCE.md`.

## Consequences

### Positive

- The department's intake is the shape already proven in production against this exact workspace,
  including the `select`-not-`status` trap and the harmless-repeat discipline.
- Readiness stays the board's rule in one place, as `n8n/README.md` demands.
- A live open question is closed with evidence instead of another round trip, and task 19 inherits
  the answer.

### Negative / Trade-offs

- Two Notion adapters now hold the same HTTP plumbing twice. That is deliberate and temporary, and
  it will look like an obvious omission to anyone who reads `notion_episode_board.py` without this
  section.
- Eight more required environment variables. A fresh `.env` gets longer, and every one of them fails
  closed at startup.
- `mode` on the board is a decided-not-asked call, and a wrong guess costs the editor one column to
  fill per episode.

## Alternatives considered

- **Reuse `BriefBoard` with a second database id.** Rejected: `IncomingBrief` carries a body of
  text, an episode carries a source handle and a cutting mode. Overloading one port would push
  `None`-shaped fields into both.
- **Put the video's location on the board.** Rejected: it makes the editorial board a storage
  index, and the local path of a test file would be baked into a Notion column that has to change
  when the video moves.
- **Extract a shared Notion HTTP client now**, since a third consumer is already foreseeable.
  Rejected by the repo's own rule of three; foreseeable is not three, and the extraction is cleaner
  when the third case shows what actually varies.
- **Infer the cutting mode from the episode** (e.g. always semantic, chunk only on request).
  Rejected: that is readiness logic in disguise — a second copy of a decision the board owns.

## References

- `PROJECT.md` §4 п.11 (extend by adding; no premature extraction), §18
- `ARCHITECTURE.md` §3.2 (n8n is transport only), §15
- ADR-0023 §6 (adapter contracts named by role), ADR-0025 (in-memory stubs), ADR-0040 (the Notion
  board: `urllib`, readiness property, required variables), ADR-0041 (status write-back that never
  fails a Run), ADR-0047, ADR-0049 §3 (trigger/polling findings), ADR-0053 §5/§7 (two cutting modes;
  the episode source port)
- `n8n/README.md` → Operator verification log (2026-09-17, 2026-09-18, 2026-09-19)
- `CLAUDE.md` queue tasks 18, 19, 21
