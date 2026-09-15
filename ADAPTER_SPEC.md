# ADAPTER_SPEC.md — Спецификация контрактов слоя адаптеров

> Технический контракт по `ADR-0023`. Подчинён `PROJECT.md` §4 п.7/§5/§6, `ARCHITECTURE.md`
> §9/§10/§12–§15 и `DOMAIN_MODEL.md` §8/§9.1 (PROJECT §17: при конфликте побеждают документы).
> Приёмка — `ADAPTER_ACCEPTANCE.md`.
>
> **Статус:** Accepted. **Дата:** 2026-09-15.

---

## 1. Назначение и границы

- **Что это.** Внутренние контракты пяти адаптеров ROADMAP Этапа 6 — единственного пути ядра во
  внешний мир. Четыре новых контракта — пакет `omemo_content_factory.adapters`; контракт LLM
  Adapter уже существует (`infrastructure/llm.py`, ADR-0014/0016) и не меняется.
- **В scope.** Протоколы `RunStore`, `BriefBoard`, `ReviewDesk`, `AnalyticsSink`; значения, которыми
  они обмениваются (`IncomingBrief`, `ReviewPackage`, `ReviewDecision`); их технические ошибки;
  проверки границ слоя.
- **Вне scope.** Любые реализации (Storage — 6b, заглушки — 6c, реальные Notion/Google Docs —
  Этапы 9–10); проводка в Content Director и точки входа; `Run.restore`/`RunSnapshot`; сущность
  Content Brief; личность рецензента; async/батчи/ретраи.

## 2. Размещение и зависимости

| Слой | Может импортировать контракт | Может импортировать реализацию |
|---|---|---|
| `domain` | нет | нет |
| `adapters` (контракты) | свой пакет | нет |
| `application`, `agents`, `skills`, `tools` | да | нет |
| `infrastructure` | да (реализует) | да |
| Composition Root (`composition.py`) | да | да (выбирает) |

Модули `adapters/` импортируют только разрешённый чистый stdlib (`__future__`, `collections.abc`,
`dataclasses`, `typing`), `domain.*` и друг друга. Вне `infrastructure/` ни один модуль не
импортирует сторонний пакет или сетевой/файловый модуль stdlib; `infrastructure` импортирует только
Composition Root.

| Адаптер (ARCH §9) | Контракт | Модуль | Ошибка |
|---|---|---|---|
| LLM Adapter | `LLMClient` | `infrastructure/llm.py` | `LLMError` |
| Storage Adapter | `RunStore` | `adapters/run_store.py` | `RunStoreError` |
| Notion Adapter | `BriefBoard` | `adapters/brief_board.py` | `BriefBoardError` |
| Google Docs Adapter | `ReviewDesk` | `adapters/review_desk.py` | `ReviewDeskError` |
| Analytics Adapter | `AnalyticsSink` | `adapters/analytics_sink.py` | `AnalyticsSinkError` |

## 3. Общие правила

1. Контракт — структурный `Protocol`, без базового класса.
2. На границе — только доменные типы, значения контракта и непрозрачные `str`-ссылки; ни имён, ни
   форматов поставщика (в идентификаторах контрактов нет «notion», «google», «sql» и т. п.).
3. Адаптер **не меняет** Run: возвращает данные, ядро применяет их через корень Run. `Run` получает
   только `RunStore` — чтобы сохранить и восстановить.
4. Внешний сбой — ошибка своего контракта (`Exception`, **не** `DomainError`). Доменная ошибка,
   возникшая в ядре во время работы адаптера, пробрасывается как есть.
5. Запись идемпотентна: повтор после сбоя/перезапуска не дублирует эффект (ADR-0015 I3).
6. Секреты через контракт не проходят; учётные данные реализация читает из окружения/секрет-стора.
7. Вызовы синхронные.

## 4. `RunStore` (`adapters/run_store.py`)

| Метод | Поведение |
|---|---|
| `save(run, /) -> None` | атомарно сохраняет **всю** текущую истину Run (состояние, дети, записи аналитики, журнал событий), заменяя сохранённое под тем же `run_id`; повтор без изменений ничего не меняет; сбой оставляет прежнюю истину целой → `RunStoreError` |
| `load(run_id, /) -> Run \| None` | тот же Run (ADR-0015 I7) либо `None`, если под `run_id` ничего нет; `RunRestorationError` (Run не допускает сохранённое) пробрасывается, не превращается в `None`/`RunStoreError` |

Снимок, формат, транзакции — детали реализации (RUN_RESTORE_SPEC §6). Один store на агрегат:
Artifact/Human Review/Evaluation/Analytics Record/журнал сохраняются вместе со своим Run.
Определения (Workflow, Schema, Agent, Prompt) здесь не хранятся (ADR-0015 §2).

**Предусловие 6b.** Состав `RunSnapshot` (RUN_RESTORE_SPEC §3) старше ADR-0018/0020 и не содержит
Evaluation, Analytics Record и их счётчиков id — спека дополняется до реализации.

## 5. `BriefBoard` (`adapters/brief_board.py`)

| Метод | Поведение |
|---|---|
| `fetch_brief(brief_ref, /) -> IncomingBrief \| None` | бриф, готовый к производству; `None` — неизвестный **или** не готовый (ядро не создаёт Run — fail closed) |
| `report_status(brief_ref, /, *, run_id, status: RunStatus) -> None` | показать статус Run на брифе; повтор того же статуса безвреден; сбой не меняет Run |

`IncomingBrief(brief_ref, body)` — frozen; оба поля — непустые `str` (иначе `ValueError`).
`brief_ref` становится `content_brief_ref` Run, `body` — вход первого шага
(`execute_workflow(..., brief=...)`). Это **не** сущность Content Brief (Этап 9).

## 6. `ReviewDesk` (`adapters/review_desk.py`)

| Метод | Поведение |
|---|---|
| `publish(package, /) -> str` | показать кандидата рецензенту; вернуть непустое место, где его найти; повторная публикация того же `review_id` возвращает то же место, а не копию |
| `fetch_decision(review_id, /) -> ReviewDecision \| None` | решение человека; `None` — ещё ждёт; неопубликованный `review_id` → `ReviewDeskError` |

`ReviewPackage(run_id, review_id, candidate: ArtifactView, brief, qa_flags=())` — frozen; иначе
`ValueError`:

| Правило |
|---|
| `run_id`, `review_id`, `brief` — непустые `str` |
| `candidate.run_id == run_id` |
| `candidate.status is ArtifactStatus.CANDIDATE` |
| `qa_flags` — кортеж непустых `str` (может быть пустым) |

`ReviewDecision(decision: ReviewStatus, reason=None)` — frozen; `decision` — `APPROVED` /
`REJECTED` / `CHANGES_REQUESTED` (не `PENDING`, не строка); `reason` — `None` или непустая `str`;
иначе `ValueError`. Применяется один-в-один:
`run.submit_review(review_id, d.decision, by=Actor.HUMAN_REVIEWER, reason=d.reason)`.

Площадка **переносит** решение, но не принимает его; одобрение по-прежнему требует `PASSED`
последней Evaluation (ADR-0018). Публикация Artifact наружу — переход Run, не вызов адаптера.

## 7. `AnalyticsSink` (`adapters/analytics_sink.py`)

| Метод | Поведение |
|---|---|
| `export(records: Sequence[AnalyticsRecord], /) -> None` | доставить копии записей в аналитический сток; запись с уже доставленным `record_id` не дублируется; сбой → `AnalyticsSinkError`, повторяется позже, Run не блокирует и не меняет |

Источник истины — записи Run (ADR-0020), сохраняемые `RunStore`; сток — копия.

## 8. Ошибки

| Ошибка | База | Когда |
|---|---|---|
| `RunStoreError` | `Exception` (техническая) | хранилище не смогло прочитать/записать |
| `BriefBoardError` | `Exception` (техническая) | доска брифов недоступна/отказала |
| `ReviewDeskError` | `Exception` (техническая) | площадка ревью недоступна/отказала; неопубликованный `review_id` |
| `AnalyticsSinkError` | `Exception` (техническая) | сток не принял записи |
| `ValueError` | — | некорректные `IncomingBrief` / `ReviewPackage` / `ReviewDecision` |
| `dataclasses.FrozenInstanceError` | — | попытка изменить значение контракта |

Ни одна из технических ошибок не наследует `DomainError` (ADR-0017).
