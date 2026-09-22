# Architecture Decision Records (ADR)

This directory holds **Architecture Decision Records** — the third level of the
project's documentation hierarchy (PROJECT.md, section 17):

```
PROJECT.md  →  ARCHITECTURE.md  →  ADR  →  Implementation (Code)
```

An ADR answers **"why was this architectural decision made?"**. After the three
architectural charters were accepted, all significant architectural changes must
be recorded as ADRs (PROJECT.md, sections 11 and 17).

## How it works

- Each decision is one Markdown file named `NNNN-short-title.md`, where `NNNN`
  is a zero-padded, monotonically increasing number.
- Start from [`0000-adr-template.md`](0000-adr-template.md).
- An ADR is immutable once **Accepted**. To change a decision, write a new ADR
  and mark the old one `Superseded by ADR-XXXX`.
- ADRs are versioned and reviewed exactly like code.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-stage-1-tooling-and-project-scaffold.md) | Stage 1 tooling and project scaffold | Accepted |
| [0003](0003-run-domain-model-interface-contract.md) | Run domain model interface contract | Accepted |
| [0004](0004-task-aggregate-run-task-interface-contract.md) | Task aggregate — Run↔Task interface contract | Accepted |
| [0005](0005-output-entity-interface-contract.md) | Output entity — Run/Task↔Output interface contract | Accepted |
| [0006](0006-artifact-entity-interface-contract.md) | Artifact entity — Run↔Artifact interface contract | Accepted |
| [0007](0007-human-review-and-artifact-publication-path.md) | Human Review entity + the Artifact approval/publication path | Accepted |
| [0008](0008-schema-entity-interface-contract.md) | Schema entity — interface contract + Output validation path | Accepted |
| [0009](0009-workflow-and-workflow-step-declarative-contract.md) | Workflow + Workflow Step — declarative interface contract | Accepted |
| [0010](0010-agent-boundary-non-execution-contract.md) | Agent boundary definition — non-execution role contract | Accepted |
| [0011](0011-agent-prompt-binding-layer.md) | Agent + Prompt binding layer | Accepted |
| [0012](0012-composition-root-application-wiring-layer.md) | Composition Root (Application Wiring Layer) | Accepted |
| [0013](0013-execution-topology-contract.md) | Execution Topology Contract | Accepted |
| [0014](0014-structured-output-port-and-generation-shape.md) | Structured Output Port and Generation Shape | Accepted |
| [0015](0015-execution-state-recoverability.md) | Execution-State Recoverability and the Admission of Restored Runs | Accepted |
| [0016](0016-provider-model-selection-ownership.md) | Provider / Model Selection Ownership | Accepted |
| [0017](0017-shared-domain-error-base.md) | Shared `DomainError` base for the per-aggregate error hierarchies | Accepted |
| [0018](0018-evaluation-qa-entity-and-fail-closed-gate.md) | Evaluation (QA) entity + the fail-closed QA gate on Artifact approval | Accepted |
| [0019](0019-artifact-versioning-and-the-rework-path.md) | Artifact versioning (`SUPERSEDED`) — the rework path | Accepted |
| [0020](0020-analytics-record-entity.md) | Analytics Record entity — append-only per-call metrics owned by Run | Accepted |
| [0021](0021-skills-library-contract.md) | Skills library — the `Skill` contract and the first three Skills | Accepted |
| [0022](0022-tool-layer-contract-and-agent-scoping.md) | Tool Layer — the `Tool` contract, the per-agent `Toolbox` and the first two Tools | Accepted |
| [0023](0023-adapter-layer-contracts.md) | Adapter Layer contracts — the LLM Adapter recognised, four new ports | Accepted |
| [0024](0024-storage-adapter-sqlite-run-store.md) | Storage Adapter — Run restoration realised, an embedded SQLite `RunStore` | Accepted |
| [0025](0025-in-memory-adapter-stubs.md) | In-memory stubs for the board, the review desk and the analytics sink | Accepted |
| [0026](0026-storage-wiring-checkpoints-and-resumption.md) | Storage wiring — the Content Director commits every step and resumes a restored Run | Accepted |
| [0027](0027-skill-invocation-at-the-task-input-boundary.md) | Skill invocation at the Task-input boundary | Accepted |
| [0028](0028-llm-tool-use-loop.md) | Provider-neutral LLM tool-use loop | Accepted |
| [0029](0029-llm-call-metrics-capture-and-pricing.md) | LLM call metrics capture and explicit token pricing | Accepted |
| [0030](0030-versioned-prompt-store.md) | Versioned Prompt Store at the Composition Boundary | Accepted |
| [0031](0031-fail-fast-task-sequencing-and-schema-binding.md) | Fail-fast Task sequencing and authoritative Schema binding | Accepted |
| [0032](0032-resumable-qa-rework-routing.md) | Resumable QA rework routing through a new Artifact version | Accepted |
| [0033](0033-invalid-output-contract-error-and-m2-acceptance.md) | Invalid Output as an orchestration contract error, and the Milestone M2 acceptance | Accepted |
| [0034](0034-qa-verdict-field-contract.md) | The QA verdict field contract — a three-way verdict and flags over flat string fields | Accepted |
| [0035](0035-qa-agent-role-definition.md) | The QA Agent role definition — `qa_agent@v1` with a baseline, review-pending Prompt | Accepted |
| [0036](0036-qa-call-metrics-attributed-to-the-evaluation.md) | QA call metrics attributed to the Evaluation, and the `LLMArtifactEvaluator` | Accepted |
| [0037](0037-domain-pivot-to-a-business-agnostic-content-factory.md) | Domain pivot — a business-agnostic content factory, uniqueness as value #1 | Accepted |
| [0038](0038-qa-evaluator-wiring-and-qa-failure-surfacing.md) | Wiring the QA evaluator into the Composition Root and an entrypoint, and how a QA failure surfaces | Accepted |
| [0039](0039-stage-8-acceptance.md) | The ROADMAP Stage 8 (QA Agent) acceptance | Accepted |
| [0040](0040-notion-brief-board.md) | Notion Adapter — a real `BriefBoard` over the Notion REST API | Accepted |
| [0041](0041-brief-status-write-back.md) | Status write-back — every committed Run status is shown on its brief | Accepted |
| [0042](0042-stage-9-acceptance-and-brief-intake.md) | The ROADMAP Stage 9 (Notion) acceptance, and the brief intake it required | Accepted |
| [0043](0043-google-docs-review-desk.md) | Google Docs Adapter — a real `ReviewDesk` over Google Drive, a service account and a decision marker | Accepted |
| [0044](0044-approval-gate-and-review-publication.md) | The Approval Gate holds every QA-passed candidate, and a pending review is published | Accepted |
| [0045](0045-review-decision-fetch-and-rejection-routing.md) | The reviewer's decision is read from the desk, and a rejection is reworked with its reason | Accepted |
| [0046](0046-stage-10-acceptance-and-review-decision-intake.md) | The ROADMAP Stage 10 (Google Docs) acceptance, and taking a review decision into the store | Accepted |
| [0047](0047-review-link-on-the-brief.md) | The review Doc's link is shown on the brief | Accepted |
| [0048](0048-brief-production-and-run-index.md) | One brief invocation as application code, and listing stored Runs by status | Accepted |
| [0049](0049-production-service-and-n8n-workflows.md) | An HTTP production service for n8n, and the n8n workflows that call it | Accepted |
| [0050](0050-stage-11-acceptance.md) | The ROADMAP Stage 11 (n8n) acceptance | Accepted |
| [0051](0051-stage-12-acceptance-and-the-m3-pilot.md) | The ROADMAP Stage 12 (MVP) acceptance, and what Milestone M3 still needs | Accepted |
| [0052](0052-per-role-max-tokens-and-explicit-thinking.md) | `max_tokens` and extended thinking belong to the role's binding | Accepted |
| [0053](0053-clipping-department-ordering-and-shape.md) | The clipping department is the next work, and the shape it takes | Accepted |
| [0054](0054-side-effecting-tools-over-injected-ports.md) | Side-effecting Tools — a thin Tool over an injected port, and the rule that they observe rather than change | Accepted |
| [0055](0055-episode-board-contract.md) | The `EpisodeBoard` port and its Notion implementation — where an episode ready to clip comes from | Accepted |
| [0056](0056-clip-qa-criteria-and-the-format-split.md) | Clip QA — what the model judges, what arithmetic judges, and why they are two different gates | Accepted |
| [0057](0057-cutting-modes-restated-storyline-extraction.md) | The two cutting modes restated — the episode's structure decides, and storyline mode runs on the transcript | Superseded by ADR-0058 |
| [0058](0058-scene-boundary-cutting-and-an-agentless-v1.md) | Cutting at scene boundaries — and what falls away when v1 has no planner agent | Accepted |
| [0059](0059-one-run-per-episode.md) | One Run per episode — the aggregate already carries it, the orchestrator does not | Accepted |
| [0060](0060-notion-review-desk.md) | A `ReviewDesk` on Notion — typed properties instead of a marker line, and its own database | Accepted |
| [0061](0061-extraction-waits-for-three-real-implementations.md) | The Notion plumbing extraction waits for three *written* implementations | Accepted |
| [0062](0062-timed-captions-on-the-clip-plan.md) | Timed captions on the clip plan and the render request | Accepted |
| [0063](0063-v1-renders-without-burning-captions.md) | v1 renders clips without burning captions, and keeps them for when it can | Accepted |
| [0064](0064-footage-index-on-ffmpeg-and-whisper-cpp.md) | `FootageIndex` on ffmpeg and whisper.cpp — the vendor is dropped | Accepted |
| [0065](0065-generation-department-ordering-and-shape.md) | The generation department is the next work, and the shape it takes | Accepted |
| [0066](0066-generation-ports-and-the-two-call-pipeline.md) | The generation ports — `ImageGenerator` and `VideoGenerator`, and a two-call pipeline | Accepted |
| [0067](0067-generation-vendor-adapters-over-urllib.md) | The generation vendor adapters — Gemini and Higgsfield over stdlib `urllib` | Accepted |
| [0068](0068-evaluation-context-and-the-recorded-footage-index.md) | An evaluation can be given its context — the episode index is recorded in the Run | Accepted |
| [0069](0069-scene-mode-never-cuts-a-line-and-scenes-need-meaning.md) | `SCENE` never cuts inside a spoken line — and real scenes need meaning, not pixels | Accepted |
| [0070](0070-clip-qa-blocks-only-what-must-not-ship.md) | Clip QA blocks only what must not ship — boundary remarks become hints | Accepted |
| [0071](0071-captions-are-burnt-at-render-time.md) | Captions are burnt in at render time, so the reviewer approves what ships | Accepted |
| [0072](0072-a-post-text-drafted-by-a-model-and-approved-with-the-clip.md) | A post text drafted by a model, shown on the review page, approved with the clip | Accepted |
| [0073](0073-auto-posting-through-a-clip-publisher.md) | Auto-posting an approved clip through a `ClipPublisher` — upload-post behind it | Accepted |
| [0074](0074-titles-and-credits-found-by-audio-fingerprint.md) | Opening titles and end credits are found by audio fingerprint, and no clip covers them | Accepted |
| [0075](0075-a-vertical-canvas-with-bars-reserved-for-banners.md) | Clips are rendered on a vertical canvas, with the bars above and below kept for banners | Accepted |
| [0076](0076-render-quality-a-4k-canvas-and-crf-18.md) | Render quality — a 4K vertical canvas and CRF 18, so the picture is not shrunk | Accepted |
| [0077](0077-a-skip-zone-swallows-the-lines-that-straddle-it.md) | A skip zone swallows the spoken lines that straddle it | Accepted |
