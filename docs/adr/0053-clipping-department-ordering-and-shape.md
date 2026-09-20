# ADR-0053: The clipping department is the next work, and the shape it takes

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Lead Architect / Domain Architect
- **Closes:** `CLAUDE.md` queue task 20 (the ordering decision). Does **not** close Milestone M3.

## Context

`CLAUDE.md` queue task 20 states the rule this ADR obeys: closing ROADMAP Stage 12 as code did not
authorize either of the two things that were waiting on it — task 9 (Stage 13, real media
production) or task 10 (`CONTENT_FACTORY_THOUGHTS.md` §16, an earlier narrow video slice) — and
neither may start without an explicit ordering ADR, *asked, not guessed*.

On 2026-09-20 the maintainer brought a **third** candidate, on branch
`feature/clipping-department`: a second department that takes an existing TV series episode (rights
cleared) and produces short vertical clips for TikTok / Instagram Reels / YouTube Shorts, through
the same fail-closed QA + Human Review discipline the content factory already enforces (ADR-0018).
Its working note, `CLIPPING_DEPARTMENT_THOUGHTS.md`, sits outside the documentation hierarchy
exactly as `CONTENT_FACTORY_THOUGHTS.md` does: it is directional, it authorizes nothing, and it
lists eight open questions for the maintainer.

Those eight were put to the maintainer and answered on 2026-09-20. Four of them turned out to have
an answer already in this repo, and were confirmed rather than invented:

- **The side-effecting Tool boundary is not a new architectural case.** ADR-0022 "Deferred" already
  says: *"Tools on top of Adapters (after Stage 6) … The import allowlist is widened explicitly, by
  ADR, and the adapter is injected like the clock."* The shape is settled — a thin Tool, the
  dependency injected as a port, the real I/O in `infrastructure/`, and
  `_ALLOWED_PROJECT_IMPORTS` in `tests/test_tool_contract.py` widened by name. What is missing is
  the ADR doing it, not a decision about whether to.
- **Synchronous versus asynchronous is two questions, not one, and both are answered.** ADR-0028 §3
  bounds a *reasoning step* at 8 operational calls with the model waiting; `ProductionService`
  (ADR-0049) already runs long work off a `202` and a single background worker. Minutes-long work
  belongs to the second mechanism and must never enter the first.
- **Readiness is the board's rule.** `BriefBoard.fetch_brief` already returns `None` for a brief
  that is not ready, and `n8n/README.md` warns against a second copy of that rule. The clipping
  department inherits the pattern unchanged.
- **A second department does not need the core to change.** The factory is business-agnostic
  (ADR-0037), `Artifact.kind` is a free string, and the ports are named by role, not by vendor
  (`DOMAIN_MODEL.md` §8). ROADMAP Stage 13's DoD already demands that a new agent or content type
  costs no change to the Content Director or the contracts.

The maintainer's four substantive answers (2026-09-20):

1. the clipping department is the next work, starting now;
2. the video-understanding vendor is **Vyra AI** (`usevyra.com`), reachable over MCP;
3. the "episode ready to clip" board is a **separate** database, not the brief board;
4. in test mode the episode is a **local video file** the maintainer supplies; v1 does **not**
   publish — an approved clip file is the deliverable and a human posts it. Automatic publishing is
   wanted later;
5. the department must cut **both ways**: by meaning (find the clip-worthy moments) and plainly by
   length (straight two-minute pieces). The first estimate — 15 two-minute clips out of a
   25-minute episode — was put back to the maintainer because 30 minutes cannot come out of 25, and
   this is the answer: it was never one mode with impossible arithmetic, it is two modes.

## Decision

### 1. The clipping department is the next work; Stage 13 and the §16 video slice stay unstarted

We will build the clipping department next, ahead of both candidates task 20 names. This is the
maintainer's call, recorded here so the queue has the explicit ADR it demanded. Task 9 (Stage 13
media production) and task 10 (the §16 video slice) remain unauthorized; this ADR does not reorder
them relative to each other, it only says neither is next.

**Milestone M3 stays open and is not blocked by this work.** M3 needs two things a session cannot
do (`CLAUDE.md` 16.3): the maintainer's decision on the `flagged` candidate the live pilot parked at
`waiting_human`, and a review desk that exists in their environment — a Google service account, or
the Notion `ReviewDesk` of task 19. Both are waiting on the maintainer, not on engineering time, so
starting the clipping department costs M3 nothing. Task 19 is not closed by this ADR, and the
clipping department will need the same desk decision when it reaches its own human gate.

### 2. It is an additive second department, not a change to the factory

Every part of it is a new module: a new board Adapter, new Agent roles with their own Prompts and
Schemas, new Tools over new ports, new Workflow steps. `Run`, `Task`, `Output`, `Artifact`,
`Evaluation`, `Human Review`, `ContentDirector` and the existing Adapters are not touched
(`PROJECT.md` §4.11: extend by adding modules, never by modifying the core). If the department
turns out to require a core change, that is a defect signal in the architecture and gets its own
ADR before any code — exactly as ROADMAP Stage 13's DoD words it.

`ROADMAP.md` Stage 13's ordering paragraph is amended in the same change to record that this
department runs before it, so the plan does not contradict this record.

### 3. Vyra AI is the vendor; the core sees a role-named port

We will use Vyra AI, as the maintainer chose. The core will not import it, name it in a contract or
depend on its call shape: it goes behind a port named by its role, in `adapters/`, with the MCP
client in `infrastructure/`, like every other outside system in this repo (ADR-0023). This is not a
hedge against the vendor choice — it is the layering rule `tests/test_adapter_contract.py` already
enforces, which is why `NotionBriefBoard` and `GoogleDocsReviewDesk` could be written as additive
modules. The port's name and methods are the follow-up ADR's business.

### 4. Nothing slow runs inside a reasoning step

Indexing and transcribing an episode take minutes. They will run as a **prior, queued step** — the
`ProductionService` shape of ADR-0049 — and never as a Tool the model waits on. By the time a
clip-planning agent reasons, the episode is already indexed, and the Tools it is granted answer
questions about that index fast enough to fit ADR-0028 §3's 8-call budget. Cutting, caption burn-in
and rendering are mechanical: they are a deterministic `Workflow` step, which this repo already
supports (`TaskExecutor` is a Protocol and `SkillPreprocessingTaskExecutor` is a non-LLM
implementation of it), not an agent decision. This matches what ROADMAP Stage 13 already predicted
for editing and overlay.

### 5. There are two cutting modes, and only one of them needs an agent

Cutting by meaning and cutting by length are different operations and will not be forced into one
pipeline:

- **Semantic mode** — the moment-finding the working note describes: the service indexes the
  episode, the planner agent asks it for moments, ranks them and justifies each candidate. This is
  where the model earns its cost.
- **Chunk mode** — an episode sliced into consecutive pieces of a given length. There is nothing for
  a model to decide here. It is a deterministic `Workflow` step end to end: no Vyra call, no LLM
  call, no ranking, and therefore effectively free. Its only judgement is where a boundary may fall,
  which is why it still wants the transcript — a cut is nudged to the nearest speech pause instead of
  landing mid-word.

Both modes end at the same place: a rendered clip, its own Artifact, its own QA verdict and its own
human gate. The mode is an input to the department, not a fork in the domain, and QA's format checks
are common to both — only the "accuracy to the source" criteria differ, because chunk mode cannot
splice and so cannot invent an exchange.

### 6. v1 ends at an approved clip file

The department's deliverable for v1 is a rendered clip that passed QA and a human Approve. It posts
nothing. Publishing adapters per platform are deferred, wanted, and out of scope until the
maintainer asks for them — at which point they are additive Adapters, the same shape as everything
else here.

### 7. The episode is a local file for now, behind its own port

In test mode the maintainer supplies a local video file. `episode_id` resolves to a location through
a port whose first implementation reads the local filesystem, so the eventual move to remote storage
is a second implementation and not a contract change.

## Deferred — each needs its own ADR before any code

- **The side-effecting Tool boundary** — the port, the injected-adapter shape, and the explicit
  widening of `_ALLOWED_PROJECT_IMPORTS` in `tests/test_tool_contract.py`. ADR-0022 "Deferred"
  reserved exactly this ADR.
- **The clip QA criteria and the platform format contract.** Two things are open and were flagged
  back to the maintainer, not guessed: what "accuracy to the source" means for scripted fiction
  (the working answer — a clip must be self-contained and must not create meaning the scene does
  not contain: no reply cut mid-word, no splice that invents an exchange, no punchline without its
  setup, no spoiler), and the target frame — the maintainer's screenshot shows a 16:9 frame
  letterboxed inside a 9:16 phone screen, which is a different decision from reframing the shot to
  9:16 and has to be stated, not inferred.
- **Volume and the human gate.** With both modes running, 30 episodes a month implies on the order
  of 450 clips and, under the current discipline, 450 human Approves — roughly 15 a day by hand.
  Nothing about the gate is negotiable (`PROJECT.md` §12, ADR-0018), so the question is whether one
  Approve may cover a batch of chunk-mode clips from one episode, or whether every clip is reviewed
  alone as the working note assumed. That is a domain decision, and it belongs in the QA ADR
  (subtask 21.2), asked rather than guessed. Chunk mode's cost is otherwise near zero: it buys no
  tokens and makes no vendor call.
- **The board** — a separate database, per the maintainer; its properties, its readiness rule and
  whether the existing Notion integration token may read it (it is described as read-only in
  `n8n/README.md`, the same obstacle task 19 names).
- **`CLIPPING_SPEC.md` / `CLIPPING_ACCEPTANCE.md`**, in the shape of the existing per-aggregate
  specs, once the ADRs above land.
- **Automatic publishing** to TikTok / Reels / Shorts.
- **A Tool returning a list.** `ToolValue` is `str | int | bool` and a Tool answers with a flat
  mapping (`tools/contract.py`); ADR-0022 deferred richer kinds. A planner that must produce several
  clip candidates therefore carries the list in its Structured Output, not in a Tool's return — or
  the kinds get widened by ADR. Decided with the planner, not here.

## Consequences

### Positive

- The queue's own rule is satisfied: the department starts from an explicit ordering record, and
  task 20 is closed the way it asked to be closed.
- Four of the working note's eight open questions are settled by pointing at decisions this repo
  already made, so the follow-up ADRs are smaller than the note assumed.
- M3 is not sacrificed: it was already waiting on the maintainer's accounts and a review decision,
  and this work does not consume either.

### Negative / Trade-offs

- A second department doubles the surface that has to stay green under one quality gate, while the
  first department's milestone is still unproven in production. That is a real cost and is accepted
  deliberately.
- Committing to Vyra AI before a bounded evaluation means the port's shape is drawn around one
  vendor's capabilities, however role-neutral its name. A second vendor may still force a contract
  revision — the port reduces that cost, it does not remove it.
- The department has no ROADMAP stage of its own, only an amended ordering paragraph. If it grows
  past a narrow slice it will need a real stage with a Definition of Done.

## Alternatives considered

- **Finish Milestone M3 first (task 19 / the flagged candidate), then clip.** Rejected by the
  maintainer. It is also not the blocker it looks like: M3's two remaining items need the
  maintainer's decision and accounts, not a session's time.
- **Start Stage 13 (real media production) and treat clipping as one of its Adapters.** Rejected:
  Stage 13 is about *generating* media from nothing, this department *reduces* existing footage.
  They share the adapter shape and nothing else, and folding them together would drag the whole §16
  vision in as a dependency.
- **Build the moment-finding in house (audio/scene signal processing).** Rejected for v1, per the
  working note: buy the video-understanding step, and revisit only if the service does not deliver.
- **Let the clip-planning agent call the video service synchronously, indexing included.** Rejected:
  it would put minutes-long work inside an 8-call reasoning budget and turn a managed failure into a
  timeout. §4 above is the alternative that was taken.

## References

- `PROJECT.md` §4 п.11 (extend by adding, never by modifying the core), §12 (nothing ships without
  Approve), §18 (Skill / Tool / Adapter boundaries)
- `ARCHITECTURE.md` §3.2 (n8n is transport), §8 (a Tool reaches the world only through an Adapter),
  §15 (layering)
- `ROADMAP.md` Stage 13 (new agents and content types; media production's architectural shape)
- ADR-0018 (fail-closed QA gate), ADR-0022 (Tool layer; "Tools on top of Adapters" deferred),
  ADR-0023 (Adapter contracts named by role), ADR-0028 §3 (the per-step call budget), ADR-0037
  (business-agnostic core), ADR-0049 (the queued production service), ADR-0051 §3 (M3 is not claimed)
- `CLIPPING_DEPARTMENT_THOUGHTS.md` (non-normative working note), `CLAUDE.md` queue tasks 9, 10, 19, 20
