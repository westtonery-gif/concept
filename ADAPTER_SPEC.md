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

**Реализация (6b, ADR-0024).** `SqliteRunStore` (`infrastructure/sqlite_run_store.py`):
встраиваемая SQLite, одна строка на Run, вся истина — JSON-документ его `RunSnapshot`
(`infrastructure/run_snapshot_codec.py`). Предусловие выполнено: состав снимка дополнен
Evaluation, Analytics Record и их счётчиками (RUN_RESTORE_SPEC 1.1). Приёмка —
`ADAPTER_ACCEPTANCE.md` §5.

**Проводка (Этап 7, ADR-0026).** `ContentDirector(..., store=...)` сохраняет Run после каждого
**шага** оркестрации, а не после каждого вызова агрегата: запуск Task — *до* вызова исполнителя;
ответ исполнителя — вместе с Output и Artifact; открытие QA-ворот — *до* вызова оценщика; вердикт;
каждый переход Run (таблица точек фиксации — ADR-0026 §2). Поэтому сохранённый Run никогда не
содержит полузаписанного шага: `SUCCEEDED` Task — с Output и его Artifact, `RUNNING` Task и
`PENDING` Evaluation — вызовы, ответ которых не был зафиксирован. `ContentDirector.resume` /
`resume_workflow` продолжает загруженный Run по его собственному состоянию: завершённая работа не
повторяется, незафиксированный вызов делается снова (повторный вход Task в `RUNNING`, в пределах её
политики попыток); Run и план, которые не совпадают, → `RunResumptionError`. Без store поведение
прежнее. Где база: `composition.build_run_store(environ)` — `OMEMO_RUN_STORE_PATH`, по умолчанию
`.omemo/runs.sqlite3`. Приёмка — `ADAPTER_ACCEPTANCE.md` §7.

## 5. `BriefBoard` (`adapters/brief_board.py`)

| Метод | Поведение |
|---|---|
| `fetch_brief(brief_ref, /) -> IncomingBrief \| None` | бриф, готовый к производству; `None` — неизвестный **или** не готовый (ядро не создаёт Run — fail closed) |
| `report_status(brief_ref, /, *, run_id, status: RunStatus) -> None` | показать статус Run на брифе; повтор того же статуса безвреден; сбой не меняет Run |

`IncomingBrief(brief_ref, body)` — frozen; оба поля — непустые `str` (иначе `ValueError`).
`brief_ref` становится `content_brief_ref` Run, `body` — вход первого шага
(`execute_workflow(..., brief=...)`). Это **не** сущность Content Brief (Этап 9).

**Реализация (6c, ADR-0025).** `InMemoryBriefBoard` (`infrastructure/in_memory_adapters.py`):
брифы в памяти процесса; сторона управления (вне протокола) — `put(brief, *, ready=True)` и
`reports(brief_ref)`. Там, где контракт молчит: статус на неизвестном брифе → `BriefBoardError`,
ничего не записано; повтор последнего отчёта `(run_id, status)` не дублируется. Приёмка —
`ADAPTER_ACCEPTANCE.md` §6.

**Реализация (Этап 9, ADR-0040).** `NotionBriefBoard` (`infrastructure/notion_brief_board.py`):
Notion REST API через stdlib `urllib`, `Notion-Version: 2022-06-28`; без новой зависимости.
`brief_ref` — id страницы (кодируется в путь целиком). «На доске» — неархивная, не удалённая
страница настроенной базы; «готов» — свойство `status`/`select` равно настроенному значению;
`body` — текст верхнеуровневых блоков с `rich_text`, по строке на блок, со всех страниц выдачи.
`report_status` пишет `status.value` и `run_id` в два настроенных свойства `rich_text`
(перезапись, поэтому повтор безвреден).

| Случай | `fetch_brief` | `report_status` |
|---|---|---|
| пустой ref; `404`; архив/корзина; другая база | `None` | `BriefBoardError`, `PATCH` не отправлен |
| не готов (пусто / другое значение); пустое тело | `None` | — |
| свойство готовности/статуса отсутствует или не того типа | `BriefBoardError` | `BriefBoardError`, `PATCH` не отправлен |
| иной не-2xx; сбой соединения/таймаут; ответ не JSON-объект или не той формы | `BriefBoardError` | `BriefBoardError` |

Настройки — `notion_settings_from_env(environ)`: `OMEMO_NOTION_TOKEN`, `OMEMO_NOTION_DATABASE_ID`,
`OMEMO_NOTION_READY_PROPERTY`, `OMEMO_NOTION_READY_VALUE`, `OMEMO_NOTION_RUN_STATUS_PROPERTY`,
`OMEMO_NOTION_RUN_ID_PROPERTY` — все обязательны, без умолчаний; отсутствующие/пустые →
`BriefBoardError` с их именами (значения и токен в сообщения и `repr` не попадают). Не проведён —
задача 13.2. Приёмка — `ADAPTER_ACCEPTANCE.md` §8.

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

**Реализация (6c, ADR-0025).** `InMemoryReviewDesk`: место — `memory://reviews/<review_id>`;
сторона управления — `decide(review_id, decision)` (рецензент) и `published(review_id)`. Там, где
контракт молчит: другой пакет под уже опубликованным `review_id` → `ReviewDeskError`, остаётся
первый; `decide` по неопубликованному → `ReviewDeskError`; первое решение окончательно — повтор
того же безвреден, другое → `ReviewDeskError`.

## 7. `AnalyticsSink` (`adapters/analytics_sink.py`)

| Метод | Поведение |
|---|---|
| `export(records: Sequence[AnalyticsRecord], /) -> None` | доставить копии записей в аналитический сток; запись с уже доставленным `record_id` не дублируется; сбой → `AnalyticsSinkError`, повторяется позже, Run не блокирует и не меняет |

Источник истины — записи Run (ADR-0020), сохраняемые `RunStore`; сток — копия.

**Реализация (6c, ADR-0025).** `InMemoryAnalyticsSink`: по одной копии на `record_id`, в порядке
первой доставки (`records`). Там, где контракт молчит: другая запись под уже доставленным (или
повторённым в пакете) `record_id` → `AnalyticsSinkError`; пакет проверяется целиком до записи, так
что отвергнутый экспорт не доставляет ничего.

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
