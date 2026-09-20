# ADR-0064: `FootageIndex` on ffmpeg and whisper.cpp — the vendor is dropped

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Lead Architect / Domain Architect
- **Supersedes:** ADR-0053 §3's choice of vendor (Vyra AI). The port and the layering it fixed are
  unchanged — only what sits behind the port.
- **Serves:** `CLAUDE.md` queue task 21.6

## Context

ADR-0053 §2 decided to **buy** the video-understanding step rather than build it, and §3 named Vyra
AI. Two things have happened since, and together they reverse the reasoning rather than merely the
choice.

**The reason to buy largely went away.** "Buy, don't build" was about *moment-finding* — ranking
what is clip-worthy, which is genuine video understanding. Then the maintainer simplified the
product to cutting at scene boundaries, and ADR-0058 removed the planner agent entirely. What
`FootageIndex` still owes is narrow: a duration, a list of scene-change timestamps, and timed
speech.

**The vendor turned out to be the wrong shape.** Vyra's MCP drives an editor **open in a browser
tab**, bound by clicking "Connect MCP"; "No browser connected" is a documented failure mode. Our
`FootageIndex.index()` is called from a Workflow step inside `factory_service.py`, triggered by n8n
on a schedule, with nobody present. Capability was never the problem — reachability was, and this
was not checked when ADR-0053 §3 assumed "MCP" meant a server-side API. That assumption was the
error.

Five alternatives the maintainer collected were read (2026-09-21): **Shorty** — a genuinely
headless MCP over Streamable HTTP, but its eight tools offer neither transcription nor scene
detection; **SynthCut** — open source, local-first, and it has exactly the three tools we want, but
it is a Node/Electron application whose packaged build is Windows-only and whose core must run as a
persistent process; **Remotion Superpowers** and **video-editor plugin** — Claude Code plugins,
tools for an agent session rather than for a service; **Remotion MCP App** — not examined.

The convergent finding is the useful one: **every one of them that transcribes uses Whisper**, and
SynthCut — the closest fit — is a wrapper over `whisper.cpp` and ffmpeg invoked as external
processes. Both of those we can invoke directly.

## Decision

### 1. `FootageIndex` is implemented on tools we already run, with no vendor

| What the port owes | Where it comes from |
|---|---|
| `duration_ms` | `ffprobe` — already used by the renderer |
| `scenes` | ffmpeg's `scdet` filter |
| `speech` | `whisper.cpp` (`whisper-cli`, JSON output with timings) |

ffmpeg is already a dependency of this department (ADR-0063). `whisper.cpp` is one Homebrew
formula, MIT-licensed, running locally and offline. **No account, no API key, no OAuth, no credits,
and no third party who can change an API underneath us.**

### 2. The port does not change, and that is the point

`FootageIndex` was specified before it had an implementation, named by its role, with the vendor
kept out of the contract (ADR-0023). Changing the vendor — from a cloud editor to two local
binaries — touches **one module in `infrastructure/`**. Nothing in `adapters/`, `application/` or
the tests of either moves.

This is the layering earning its keep, so it is worth saying plainly: the port was not
over-engineering, and the first thing it bought was the freedom to be wrong about the vendor.

### 3. Boundary nudging keeps using the transcript, not `silencedetect`

ffmpeg's `silencedetect` was verified available and would report silent ranges. It is **not** used:
Whisper already returns word-level timings, so the gaps between spoken words are known exactly and
for free, and a second, differently-derived notion of "a pause" would be two answers to one
question. `silencedetect` stays available if the transcript ever proves insufficient.

### 4. What is given up

A real video-understanding service could have answered questions these two cannot: who is on
screen, what a shot contains, whether a moment is interesting. **v1 asks none of them** — that is
what ADR-0058 removed. If the department ever wants them back, this ADR is what to revisit, and the
port is where a vendor would re-enter.

## Deferred

- **The model file.** `whisper.cpp` ships no weights; the operator downloads a GGML model and
  names it in configuration. `large-v3-turbo` is the suggested default: the material is Russian,
  where the small models degrade audibly, and this step is a queued background job rather than an
  interactive one.
- **Scene-detection tuning.** `scdet`'s threshold is a real-footage question; a first episode
  decides it, not a guess here.
- **TwelveLabs**, named by one of the plugins, as the video-understanding vendor if §4's questions
  ever return.

## Consequences

### Positive

- The department loses its last external account and can run entirely offline.
- Two dependencies the department already needed, instead of one it did not.
- A wrong vendor choice cost one module, because the port had kept it out of everything else.

### Negative / Trade-offs

- Transcription now costs CPU time on the operator's machine instead of a vendor's, and a
  25-minute episode is minutes of work. The indexing step was already queued and slow by design
  (ADR-0053 §4), so this lands where it was expected to.
- A multi-gigabyte model file becomes part of the operator's setup.
- Scene detection by `scdet` is a luminance heuristic, not an understanding of the footage. It will
  miss a cut between two visually similar shots and invent one across a flash. The first real
  episode will show how much that matters.

## Alternatives considered

- **Stay with Vyra.** Rejected: it cannot be reached without a human at a browser.
- **SynthCut.** The closest fit and genuinely capable, and its core *is* headless. Rejected on cost
  of setup: building from source on macOS (no packaged build), a persistent Node core beside
  `factory_service.py`, an MCP client dependency in a repo with two third-party packages total, and
  a frame-based API against our millisecond contract — all to reach `whisper.cpp` and ffmpeg, which
  it invokes as external processes anyway.
- **Shorty.** Headless and well-built, but offers neither capability the port needs.
- **A Whisper API instead of local.** About $4–5 a month at this volume, and faster. Rejected for
  now because local costs nothing, needs no account, and keeps the footage on the maintainer's
  machine — which for licensed television is not a small point.

## References

- ADR-0023 (ports named by role, vendor kept out), ADR-0053 §2/§3/§4 (buy-not-build; the vendor;
  slow work is queued), ADR-0058 (no planner agent), ADR-0063 (ffmpeg)
- `CLIPPING_SPEC.md` §5; `CLIPPING_ACCEPTANCE.md` (FIX)
