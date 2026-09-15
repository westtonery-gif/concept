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
