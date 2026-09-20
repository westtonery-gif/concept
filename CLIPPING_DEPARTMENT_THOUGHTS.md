# Clipping Department — Planning Notes (non-normative)

> **Status:** working note from a design conversation on 2026-09-20, captured on branch
> `feature/clipping-department`. Like `CONTENT_FACTORY_THOUGHTS.md`, this sits **outside** the
> documentation hierarchy (`CLAUDE.md`) — it does not override `PROJECT.md` / `ARCHITECTURE.md` /
> `ROADMAP.md`, and it does not itself authorize starting code. Per this repo's spec-before-code
> process (`CLAUDE.md` "Conventions": "No code before its spec/acceptance/ADR exist"), the open
> questions below need the maintainer's answers and at least one ADR before anything here gets
> implemented.

## Goal

A second department alongside the existing content factory: take an existing TV series episode
(rights already cleared — maintainer confirmed) and produce short vertical clips for TikTok /
Instagram Reels / YouTube Shorts, going through the **same fail-closed QA + Human Review**
discipline the content factory already enforces (ADR-0018, PROJECT.md — nothing ships without
Approve).

## Design decisions reached in conversation (2026-09-20)

These are **directional**, not binding — write them into an ADR before code, don't just implement
from this doc.

1. **Agent reasons, service/Tool executes.** Same Skill/Tool/Agent split the content factory
   already uses (ADR-0010/0011/0021/0022). The clip-planning agent does not cut video itself — it
   decides *which* moments and *why*, and outputs a structured plan (Schema-validated, same shape
   as Rin/Leo's Structured Output). Cutting, caption burn-in and rendering are mechanical
   execution, not agent judgment — this matches what `CLAUDE.md` queue task 9 already says about
   Stage 13: *"editing/overlay likely a deterministic Workflow step rather than an agent
   decision."*

2. **Buy, don't build, the video-understanding step (v1).** Rather than building a custom
   audio/scene signal-processing pipeline (laughter/volume-spike/shot-detection) in house, delegate
   moment-finding to a third-party AI video editor reachable via MCP. **Candidate: Vyra AI**
   (`usevyra.com`) — indexes video content and exposes editing/search over MCP (streamable HTTP);
   not committed, the maintainer is still evaluating alternatives. The agent's job becomes:
   converse with the service ("find moments matching X"), evaluate what it returns, rank and
   justify. Revisit the in-house signal-processing approach only if the chosen service doesn't
   deliver.

3. **Readiness is a board's job, not the agent's.** Mirrors the content factory's Notion
   `Stage = Ready for production` pattern and the explicit warning already in `n8n/README.md`:
   *"Readiness is the board's rule... an IF node here would be a second copy of it."* Whichever
   episode is "ready to clip" should be decided by an external trigger/board, not re-decided inside
   the agent's reasoning.

4. **One episode → multiple clip candidates, each its own gate.** A 25-minute episode likely
   yields several clip-worthy moments. The planner agent's output should be a **list** of
   candidates (timestamps + rationale + draft caption), and each candidate probably needs its own
   QA + Human Review — an Artifact per clip, not one bulk decision for the whole episode. Mirrors
   how each script version already gets its own approval gate.

5. **Side-effecting Tools are a new architectural case, not yet decided.** Today's `tools/` package
   is pure-stdlib only (enforced by `tests/test_tool_contract.py` — no network, no subprocess, no
   SDK). A tool like `transcribe_episode(episode_id)` (download → ffmpeg → STT API → save) or an
   MCP-backed Vyra tool both have real side effects and cost. This is the **same open architectural
   question** for both — resolve it once, not twice. Likely shape (by analogy with `adapters/` vs
   `infrastructure/`): a thin `Tool` in `tools/` delegating to an injected port, with the real I/O
   living in `infrastructure/`. Not decided.

## Open questions — need the maintainer, then an ADR (don't guess past these)

1. **Video service:** Vyra AI vs an alternative — final pick, or build a thin internal abstraction
   so the actual vendor is swappable without touching the agent/Tool contract?
2. **Board/trigger:** a new Notion database mirroring the brief board (`episode ready to clip`),
   or a different mechanism entirely?
3. **Source video access:** where does the episode file live, and how does `episode_id` resolve to
   a downloadable/streamable location?
4. **Tool boundary:** does `tools/`'s "pure stdlib only" rule get an explicit, scoped exception for
   side-effecting Tools, or does execution move fully into `infrastructure/` behind a thin wrapper?
   (Item 5 above — needs its own ADR.)
5. **Sync vs async execution:** the Tool-use loop (ADR-0028) assumes fast "operational calls" (an
   8-call budget per step) with the model waiting synchronously. STT + cutting + rendering can run
   minutes, not milliseconds. Does the agent block on a slow call, or does this need a queued/poll
   pattern like `factory_service.py` already has for brief production?
6. **Clip QA criteria:** what makes a clip pass — accuracy to source (no misleading cut), platform
   format compliance (aspect ratio, duration caps per platform), brand/content safety? Needs its
   own Prompt + Schema, same shape as `qa-agent`.
7. **Publishing scope for v1:** does the department post to TikTok/IG/YouTube Shorts itself (a new
   Adapter per platform), or does v1 stop at "approved clip file, ready for a human to post
   manually"?
8. **Cost/volume bounds:** Vyra (or alternative) pricing, STT pricing per episode, expected episode
   volume — needed before committing to a per-call/async design.

## Suggested task order for whoever picks this up next

1. **Answer the open questions above with the maintainer.** This is the actual first task — do not
   skip straight to code or even to an ADR draft without these answers, per this repo's
   "asked, not guessed" convention throughout its ADR history.
2. **Write the ADR(s)** resolving at least: the side-effecting Tool boundary (item 4/5 above,
   applies to both a transcription tool and any Vyra/MCP tool), and the buy-vs-build video
   understanding decision (item 1).
3. **Write `CLIPPING_SPEC.md` / `CLIPPING_ACCEPTANCE.md`** in the same shape as the content
   factory's per-aggregate specs, once the ADR(s) land.
4. **Only then implement:** the board adapter (mirrors `BriefBoard`), the clip-planner Agent
   (Prompt + Schema + Tool grants), the QA role (reuse `qa-agent`'s shape with clip-specific
   criteria), and the execution Tools/Workflow steps.

## Explicitly out of scope for v1 (until reconsidered)

- A custom in-house signal-processing highlight-detection pipeline — only if the chosen video
  service doesn't deliver on moment-finding.
- Automatic publishing to social platforms — pending the answer to open question 7.
- Multi-tenant / per-client editing rules — mirrors the same deferral the content factory already
  made for client profiles.
