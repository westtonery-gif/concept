# AGENT_SPEC.md — Доменная спецификация сущностей **Agent** и **Prompt**

> **Место в иерархии документации (PROJECT.md, раздел 17).**
> `PROJECT.md` → `ARCHITECTURE.md` → `ROADMAP.md` → `DOMAIN_MODEL.md` →
> **`AGENT_SPEC.md`** → `ADR` → `Implementation (Code)`.
>
> Спецификация двух доменных сущностей-определений — `Agent` и `Prompt` — как **чистых
> неизменяемых дескрипторов**. Опирается на `DOMAIN_MODEL.md` §2.5/§2.7/§9.3 и на
> `ADR-0010` (Agent boundary), `ADR-0011` (Agent = descriptor / Prompt = immutable),
> `ADR-0012` (Composition Root — dumb wiring), `ADR-0013` (Execution Topology + Authority).
> Только предметная область: без кода, типов, исполнения и инфраструктуры.

**Версия документа:** 1.2
**Статус:** Принят (контракт для реализации модели Agent / Prompt)
**Дата:** 2026-09-15
**Владелец:** Архитектор предметной области

---

## 1. Overview

**Agent** — **неизменяемый дескриптор** роли. Его единственная суть: связать `agent_ref` с
`prompt_id` (через `prompt_ref`). Это запись каталога определений (`DOMAIN_MODEL.md` §2.5, §9.3),
**чистые данные**: Agent не исполняется, не оркестрирует, не принимает решений и не влияет на
Workflow (`ADR-0010`, `ADR-0011`). Он **описывает**, а не действует.

---

## 2. Model

**Agent:**

- **`agent_id`** — стабильная неизменяемая идентичность.
- **`name`** — человекочитаемое имя роли.
- **`prompt_ref`** — ссылка на `prompt_id` активного Prompt (активная привязка **1:1**, `ADR-0011`).
- **optional metadata (non-execution)** — необязательные **описательные** метаданные, не влияющие
  на исполнение (например, краткое описание зоны ответственности). Они не читаются на этапах
  planning/ordering/execution.
- **`skill_refs`** — упорядоченный кортеж ссылок на Skills роли (`ADR-0027`); по умолчанию пуст.
  Это пассивная конфигурация: composition root сопоставляет ссылки со статическими invocation-
  binding'ами и строит executor-decorator, а Agent ничего не вызывает. Порядок фиксирует порядок
  последовательных преобразований входа.
- **`tool_refs`** — выдача Tools роли (`ADR-0022`, `TOOL_SPEC.md` §5): кортеж ссылок
  `<tool_id>@v<n>`; по умолчанию пуст — у роли нет Tools. Это данные, как `prompt_ref`: читаются
  только при сборке `Toolbox` в composition root; сама роль ничего не вызывает.

---

## 3. Invariants

1. **no execution semantics** — Agent ничего не исполняет.
2. **no orchestration** — Agent не управляет конвейером и не выбирает порядок.
3. **no decision logic** — Agent не содержит условной логики и не принимает решений.
4. **no workflow influence** — Agent не влияет на Workflow и на порядок шагов.
5. **immutable** — Agent неизменяем после создания; новая ревизия = новое значение.
6. **ровно один активный Prompt** — Agent ссылается на ровно один активный Prompt (`prompt_ref`,
   active 1:1; `DOMAIN_MODEL.md` §9.3).
7. **Skill declaration is passive** — `skill_refs` только объявляет версии и порядок; исполняемый
   binding существует вне Agent и сверяется в composition root (`ADR-0027`).

---

## 4. Prompt contract

- **immutable text artifact** — Prompt есть неизменяемый текстовый артефакт (System/User текст);
  после фиксации не меняется.
- **versioned** — Prompt идентифицируется `prompt_id` + версия (Value Object); новая ревизия =
  новая версия. Зафиксированная версия неизменяема (`DOMAIN_MODEL.md` §2.7, §6).
- **execution-time only usage** — текст Prompt используется **только во время исполнения Task**
  (читается на этапе wiring, применяется внутри executor); он не используется в planning/ordering и
  не влияет на Workflow.
- **output contract (reference to the Schema authority)** — `Prompt.schema_ref` (версия Schema;
  опаковая ссылка) — это **ссылка** на авторитетную Schema, против которой валидируется результат
  исполнения. **Authority над контрактом — Schema layer** (`DOMAIN_MODEL.md` §2.7, §2.8; `ADR-0008`);
  Prompt остаётся **passive artifact** (`ADR-0011`): несёт текст и ссылку, контракт **не определяет и
  не валидирует**. `WorkflowStep.schema_ref` — декларативный planning-hint, в runtime не участвует
  (WORKFLOW_SPEC §3). Источник истины контракта один — Schema; двойного authority нет.
- Prompt **не содержит** оркестрационной/условной логики.

---

## 5. Resolution rules

Разрешение `agent_ref → Agent → prompt` и сопоставление `Agent.skill_refs` со статическими
invocation-binding'ами происходит **ТОЛЬКО** в
**composition root wiring** (`ADR-0012`, `ADR-0013`):

- **never in ContentDirector** — CD не читает Agent/Prompt.
- **never in Workflow** — Workflow — чистые декларативные данные, дескрипторы не разрешает.
- **never in executor logic** — executor не выполняет lookup Agent/Prompt.

*Уточнение (согласованность с `ADR-0013` §8):* использование `agent_ref` в ContentDirector — это
**выбор предсобранного executor** из карты (selection), а **не** разрешение Agent/Prompt. Само
разрешение дескрипторов — только в composition root.

### 5.1 Production Prompt storage

Production-текст Prompt хранится вне Python role-модулей в версионируемом пакетном каталоге и
материализуется Composition Root на build-time (`PROMPT_STORE_SPEC.md`, `ADR-0030`). Это меняет
источник данных, но не доменный контракт: Agent по-прежнему несёт только логический `prompt_ref`, а
Prompt после загрузки остаётся пассивным неизменяемым артефактом. Формат файла, I/O и ошибки
загрузки не видны домену, executor или ContentDirector.

---

## 6. Anti-goals

- **no runtime execution role** — Agent не получает исполнительной роли во время выполнения.
- **no AI routing logic** — никакой маршрутизации/выбора на стороне Agent.
- **no dynamic prompt generation** — Prompt статичен и неизменяем; не генерируется «на лету».

---

## Приложение A. Трассируемость

| Раздел AGENT_SPEC | Опора |
|---|---|
| 1 Overview / Agent = дескриптор | `DOMAIN_MODEL.md` §2.5, §9.3; `ADR-0010`, `ADR-0011` |
| 2 Model | `DOMAIN_MODEL.md` §2.5; `ADR-0011`, `ADR-0022`, `ADR-0027` |
| 3 Invariants | `DOMAIN_MODEL.md` §6; `ADR-0010`, `ADR-0011`, `ADR-0027` |
| 4 Prompt contract | `DOMAIN_MODEL.md` §2.7, §6; `ADR-0011`, `ADR-0008` |
| 5 Resolution rules | `ADR-0012`, `ADR-0013` (§8 selection vs resolution), `ADR-0027`, `ADR-0030` |
| 6 Anti-goals | `ADR-0010`, `ADR-0011` |

> При расхождении исправляется **этот** документ: источник истины по домену —
> `DOMAIN_MODEL.md`, технические контракты — `ADR-0010…0013`, высший источник — `PROJECT.md`
> (раздел 17).
