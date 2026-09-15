# ADR-0027: Skill invocation at the Task-input boundary

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 7 (Milestone M2) requires a real Agent to use a Skill. `ADR-0021` delivered the
Skill contract and three deterministic Skills, but deliberately deferred both `Agent.skill_refs`
and runtime consumption until the first consumer existed. Today the two production roles are
passive `Agent + Prompt + Schema` definitions, and the Composition Root builds one
`LLMTaskExecutor` for each role. No path invokes a Skill.

The first consumer needs to answer four questions without changing the `Run` reference aggregate:

1. where the Agent declares the Skill it uses;
2. where typed Skill input is assembled from a Task's string input;
3. when the Skill runs relative to the external LLM call and persistence checkpoints;
4. how composition fails before execution when the declaration and runtime binding disagree.

`normalize_terminology@v1` is chosen for the first slice. Rin (`content_researcher@v1`) receives
the raw brief, so normalising the factory's brand spelling before research is a real, narrow use of
the existing Skill. The glossary remains caller configuration, as required by `SKILL_SPEC.md` §4;
the Skill itself still hardcodes no vocabulary.

## Decision

### 1. Agent declares ordered `skill_refs`

`Agent.skill_refs: tuple[SkillRef, ...] = ()` is added as passive, immutable configuration. Empty
means that the role uses no Skills. The tuple is ordered because successive input transformations
can be non-commutative; it contains only references and gives the Agent descriptor no executable
behaviour, branching or orchestration authority.

Rin declares `("normalize_terminology@v1",)`. Leo keeps the fail-closed default `()`.

### 2. A Task-input invocation adapts one typed Skill to the executor boundary

The application layer owns a small `TaskInputSkillInvocation` Protocol:

- `skill_ref -> SkillRef` identifies the invoked Skill version;
- `apply_to(task_input: str, /) -> str` adapts the Task executor's string boundary to and from the
  Skill's own typed input/output.

The first implementation, `NormalizeTerminologyBeforeTask`, owns a glossary, constructs
`NormalizeTerminologyInput`, invokes `NormalizeTerminology.apply`, and returns
`NormalizedText.text`. It contains no model, I/O, clock, Run, Task or routing decision. Its static
glossary is validated when the invocation is constructed, so a bad role configuration fails while
the runtime graph is being imported/built, not after a Run starts.

This is an invocation adapter, not a new Skill contract and not a runtime Skill registry. The
Skill remains directly imported code (`ADR-0021` §6); the adapter only supplies caller-specific
input configuration at the Task-executor boundary.

### 3. Skills decorate the executor before the external call

`SkillPreprocessingTaskExecutor(delegate, invocations)` implements the unchanged `TaskExecutor`
port. On `execute(task_input)` it applies invocations in declared order, feeding each returned
string to the next, then calls the delegate exactly once with the final string. The delegate's
`ExecutionResult` is returned unchanged.

The Content Director and `task_execution` are unchanged. Therefore:

- `Run.open_task` stores the original input before any Skill runs;
- the deterministic Skill runs after the Task-start commit and immediately before the executor's
  external/model call;
- a resumed `RUNNING` Task re-applies the Skill to that same stored original input;
- there is no separate persistence checkpoint for a pure deterministic operation;
- Skill/configuration errors are programming errors and escape loudly; provider failures keep the
  delegate's existing managed `ExecutionResult` path.

### 4. Composition validates declarations against invocations

The role modules expose static `SKILL_INVOCATIONS` data keyed by `agent_ref`. The Composition Root
accepts that mapping as an optional build input. For every Agent, the invocation refs must equal
`Agent.skill_refs` exactly, including order. A missing, undeclared, duplicated or reordered
invocation raises `CompositionError` before any model call or Run mutation. An Agent with no refs
needs no entry and keeps the existing bare executor.

The Root still performs only structural wiring: it does not call a Skill, inspect a glossary,
interpret its result or decide Task state. It builds the base executor, validates references, and
wraps it when the role declares invocations.

### 5. First production configuration

Rin's role assets include a `NormalizeTerminologyBeforeTask` configured with the mapping
`"омемо" -> "OMEMO"`. Its `skill_refs` are derived from that static invocation tuple, so the role
definition has one source for the exact version handle. Tests prove that a brief containing the
Cyrillic variant reaches the model with the canonical brand spelling while the Run's Task retains
the original brief.

## Deferred

- Post-execution Skills and policy over Skill reports (for example, turning missing required
  elements into QA routing) belong with the first such consumer; they are not generalised now.
- Persisting a per-invocation Skill trace or `NormalizedText.replacements` in the Run. The Agent
  version declares the Skill version, but the current Run contract has no Skill-invocation entity.
- Dynamic Skill selection. Skills are static role composition, not capabilities selected by the
  model; that remains the Tool boundary.
- Skill lifecycle status (`Active` / `Deprecated`) remains deferred until more than one version is
  catalogued or a deprecation consumer exists.

## Consequences

### Positive

- A real production Agent now invokes an existing Skill on every execution through the normal
  Content Director / TaskExecutor path.
- The `Run`, `Task`, Content Director and LLM port are unchanged; the slice is an additive executor
  decorator plus build-time wiring.
- Retry and crash recovery are deterministic: the same stored input passes through the same pure
  Skill configuration before each real attempt.
- Agent-to-Skill references become falsifiable at build time instead of documentation-only data.

### Negative / Trade-offs

- The first adapter supports input preprocessing only. A future output/report consumer will need a
  separately specified boundary rather than being forced into this shape.
- The replacement audit returned by `normalize_terminology@v1` is not persisted yet.
- Callers assembling multiple role catalogues must merge their `SKILL_INVOCATIONS` mappings just as
  they already merge Agent, Prompt and Schema catalogues.

## Alternatives considered

- **Invoke the Skill inside Content Director.** Rejected: the orchestrator would learn role
  behaviour and need changes for every new Skill, contradicting `ARCHITECTURE.md` §16.
- **Put executable callables on `Agent`.** Rejected: Agent is a passive immutable descriptor
  (`ADR-0010` / `AGENT_SPEC.md`); only references belong there.
- **Teach `LLMTaskExecutor` about concrete Skills.** Rejected: it would couple the provider adapter
  to role logic and typed Skill contracts.
- **Resolve Skills dynamically from `SKILLS_BY_REF`.** Rejected: `ADR-0021` intentionally keeps the
  catalogue passive, and heterogeneous typed inputs require caller configuration anyway.
- **Use `check_required_elements@v1` and fail the Task on a missing phrase.** Rejected for the first
  slice: converting that report into routing is QA policy (Stages 7.6/8), not a neutral invocation
  mechanism.

## References

- `PROJECT.md`: §4.11, §14, §18
- `ARCHITECTURE.md`: §6, §7, §15, §16
- `ROADMAP.md`: Stage 7
- `DOMAIN_MODEL.md`: §2.5, §2.6, §9.3, §9.5
- `ADR-0010`, `ADR-0012`, `ADR-0013`, `ADR-0015`, `ADR-0021`, `ADR-0026`
- `AGENT_SPEC.md` §2/§5; `SKILL_SPEC.md` §7; `SKILL_ACCEPTANCE.md` §7
