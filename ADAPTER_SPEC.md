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

**`RunIndex` (Этап 11, ADR-0048).** Отдельный протокол в том же модуле: `run_ids(*, status:
RunStatus) -> tuple[str, ...]` — id сохранённых Run в этом статусе, по возрастанию id; сбой чтения
или недекодируемая строка → `RunStoreError` (листинг никогда молча не пропускает Run). `RunStore` не
изменён — обёртки вроде `BriefStatusReporter` остаются просто `RunStore`. `SqliteRunStore`
реализует оба: читает все строки по порядку id и декодирует снимок тем же кодеком (без
`Run.restore`, без новой колонки). `composition.build_run_index(environ)` — над тем же файлом, что
`build_run_store`. Приёмка — `ADAPTER_ACCEPTANCE.md` §5 (STO-12).

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
| `report_review_location(brief_ref, /, *, run_id, location) -> None` | показать на брифе, где последнее опубликованное ревью Run (ADR-0047); `location` — непрозрачный ответ площадки; повтор безвреден; пустые `brief_ref`/`location` → `BriefBoardError`; сбой не меняет Run |

`IncomingBrief(brief_ref, body)` — frozen; оба поля — непустые `str` (иначе `ValueError`).
`brief_ref` становится `content_brief_ref` Run, `body` — вход первого шага
(`execute_workflow(..., brief=...)`). Это **не** сущность Content Brief (Этап 9).

**Реализация (6c, ADR-0025).** `InMemoryBriefBoard` (`infrastructure/in_memory_adapters.py`):
брифы в памяти процесса; сторона управления (вне протокола) — `put(brief, *, ready=True)` и
`reports(brief_ref)`, `review_locations(brief_ref)`. Там, где контракт молчит: статус или место
ревью на неизвестном брифе → `BriefBoardError`, ничего не записано; повтор последнего отчёта
`(run_id, status)` / `(run_id, location)` не дублируется. Приёмка —
`ADAPTER_ACCEPTANCE.md` §6.

**Реализация (Этап 9, ADR-0040).** `NotionBriefBoard` (`infrastructure/notion_brief_board.py`):
Notion REST API через stdlib `urllib`, `Notion-Version: 2022-06-28`; без новой зависимости.
`brief_ref` — id страницы (кодируется в путь целиком). «На доске» — неархивная, не удалённая
страница настроенной базы; «готов» — свойство `status`/`select` равно настроенному значению;
`body` — текст верхнеуровневых блоков с `rich_text`, по строке на блок, со всех страниц выдачи.
`report_status` пишет `status.value` и `run_id` в два настроенных свойства `rich_text`
(перезапись, поэтому повтор безвреден). `report_review_location` пишет `location` в настроенное
свойство типа `url` (ADR-0047); пустое место, страница не на доске, свойство отсутствует или не
`url` → `BriefBoardError` до `PATCH`.

| Случай | `fetch_brief` | `report_status` |
|---|---|---|
| пустой ref; `404`; архив/корзина; другая база | `None` | `BriefBoardError`, `PATCH` не отправлен |
| не готов (пусто / другое значение); пустое тело | `None` | — |
| свойство готовности/статуса отсутствует или не того типа | `BriefBoardError` | `BriefBoardError`, `PATCH` не отправлен |
| иной не-2xx; сбой соединения/таймаут; ответ не JSON-объект или не той формы | `BriefBoardError` | `BriefBoardError` |

Настройки — `notion_settings_from_env(environ)`: `OMEMO_NOTION_TOKEN`, `OMEMO_NOTION_DATABASE_ID`,
`OMEMO_NOTION_READY_PROPERTY`, `OMEMO_NOTION_READY_VALUE`, `OMEMO_NOTION_RUN_STATUS_PROPERTY`,
`OMEMO_NOTION_RUN_ID_PROPERTY`, `OMEMO_NOTION_REVIEW_LINK_PROPERTY` — все семь обязательны, без
умолчаний; отсутствующие/пустые →
`BriefBoardError` с их именами (значения и токен в сообщения и `repr` не попадают). Строится
`composition.build_brief_board(environ)`; бриф → Run — `application.brief_intake.produce_brief`
(ADR-0042), вызываемый из `demo_notion.py`. Приёмка —
`ADAPTER_ACCEPTANCE.md` §8.

**Обратная запись статусов (Этап 9, ADR-0041).** `BriefStatusReporter(store, board)`
(`application/brief_status.py`) — сам `RunStore`, обёртка над настоящим store:

| Операция | Поведение |
|---|---|
| `save(run)` | сначала `store.save(run)`; затем `board.report_status(run.content_brief_ref, run_id=run.run_id, status=run.status)`, один раз на смену статуса: только если последняя попытка этого репортёра для Run была с другим статусом. Сбой `save` (`RunStoreError`) пробрасывается, отчёта нет |
| `load(run_id)` | `store.load(run_id)` без изменений |
| `sync(run)` | отчёт о текущем статусе, если он ещё не был **успешно** показан; без сохранения — это повтор отказанного отчёта |

Показывается **каждая** смена статуса, которую фиксирует Content Director (`queued`, `running`,
`waiting_qa`, `waiting_human`, `completed`, `failed`; `created` не сохраняется и не показывается) —
и никогда раньше, чем статус сохранён. `ContentDirector` не изменён: он уже вызывает `save` в каждой
точке смены статуса (ADR-0026 §2). Память «последняя попытка» и «показан» — в процессе, по
`run_id`; новый процесс
показывает статус снова (повтор безвреден).

`BriefBoardError` из `report_status` **не останавливает** прогон и не меняет Run: `WARNING` в логгер
`omemo_content_factory.application.brief_status`, запись в `failed_reports`
(`FailedReport(run_id, status, message)`), статус не считается показанным. Следующие `save` в том же
статусе его **не** повторяют (иначе при отказе Notion каждый коммит ждал бы таймаут); его заменит
отчёт о следующем статусе или повторит `sync`. Любое другое исключение доски пробрасывается. `demo_notion.py` оборачивает свой store, а
`produce_brief` вызывает `sync(run)` в конце каждого запуска (в том числе после
`MeasuredEvaluatorError`); демо печатает неудавшиеся отчёты. Приёмка — `ADAPTER_ACCEPTANCE.md` §9.

**Ссылка на ревью (Этап 11, ADR-0047).** `show_review_location(run, location)` — показать место
ревью через `board.report_review_location`, только если этот репортёр ещё не показал **успешно**
это же место для Run (иначе запросов нет: запись на страницу снова будит n8n, опрашивающий правки).
Ничего не сохраняет, Run не трогает. `BriefBoardError` → `WARNING`,
`FailedLocationReport(run_id, location, message)` в `failed_location_reports`, место не считается
показанным — следующий вызов повторит; прочие исключения пробрасываются.
`shown_review_location(run_id)` — последнее успешно показанное место. `demo_notion.py` вызывает его
после публикации ожидающего ревью.

**Приём брифа (Этап 9, ADR-0042).** `produce_brief(director, store, board, workflow, *, brief_ref,
run_id) -> Run | None` (`application/brief_intake.py`), где `store` — `BriefStatusReporter`, через
который коммитит `director`:

| Случай | Поведение |
|---|---|
| Run под `run_id` не сохранён, `fetch_brief` → `None` | `None`: Run не создан, моделей и отчётов нет |
| Run не сохранён, бриф есть | `Run.create(run_id, content_brief_ref=brief.brief_ref, workflow_version_ref=workflow.workflow_id)` + `execute_workflow(brief=brief.body)` |
| сохранённый Run другого брифа | `BriefIntakeError`, Run не изменён |
| сохранённый Run с Task | `resume_workflow` с входом первой Task |
| сохранённый Run без Task (сбой до первой Task) | бриф запрашивается с доски заново; `None` → `BriefIntakeError`, Run не изменён |
| конец: обычный возврат или `MeasuredEvaluatorError` | `store.sync(run)`, затем исключение (если было) пробрасывается; прочие исключения — без `sync` |

Приёмка Этапа 9 — `STAGE9_ACCEPTANCE.md` (`S9A`).

**Один вызов брифа (Этап 11, ADR-0048).** `BriefProduction(director, store, board, workflow, *,
desk=None, index=None)` (`application/brief_production.py`) — то, что делает точка входа при каждом
срабатывании брифа (CLI `demo_notion.py` и HTTP-сервис — один и тот же код). `run_id_for_brief(ref)`
= `run-notion-<ref>` (написание сохранено ради уже сохранённых Run). `invoke(brief_ref) ->
BriefInvocation(brief_ref, run_id, run, decision, decision_error, qa_error, published,
publish_error)`:

| Шаг | Поведение |
|---|---|
| 1. с площадкой | `take_review_decision(store, desk, run_id)` → `decision`; `ReviewDeskError` → `decision_error`, вызов продолжается |
| 2. | `produce_brief(...)` → `run`; `MeasuredEvaluatorError` → `qa_error` = «`<тип>: <сообщение>`», `run` загружается из store (`waiting_qa`) |
| 3. с площадкой и Run | `publish_pending_review(run, desk)` → `published` и `store.show_review_location(run, location)`; `ReviewDeskError` → `publish_error` |
| прочее | `BriefIntakeError`, `BriefBoardError`, `RunStoreError`, доменные ошибки, дефекты — пробрасываются |

`run` = `None` только если нет ни сохранённого Run, ни готового брифа. `waiting_briefs()` — брифы Run
в `waiting_human` по `index.run_ids(status=WAITING_HUMAN)`, в порядке id, **только** Run, чей id —
`run_id_for_brief(content_brief_ref)` (Run других точек входа в том же файле пропускаются); Run,
исчезнувший между листингом и загрузкой, пропускается; без `index` → `ValueError`. Свойства `store`,
`has_desk`. Приёмка — `ADAPTER_ACCEPTANCE.md` §13 (BPR).

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

**Реализация (Этап 10, ADR-0043).** `GoogleDocsReviewDesk`
(`infrastructure/google_docs_review_desk.py`): Google Drive API v3 через stdlib `urllib`; вход —
сервисный аккаунт (JWT RS256 → access token по `token_uri` ключа, кэш до истечения минус 60 с,
сброс после `401`); единственная новая зависимость — `cryptography` (подпись RS256). Ревью — Google
Doc в настроенной папке (общие диски поддержаны), созданный конвертацией `text/plain` при загрузке;
находится по `appProperties.omemo_review` = SHA-256 `review_id`, пакет сверяется по
`appProperties.omemo_package` = SHA-256 канонического JSON пакета. Место —
`https://docs.google.com/document/d/<id>/edit`.

Текст Doc: строка-инструкция, `РЕШЕНИЕ:`, `ПРИЧИНА:`, разделитель
`======== МАТЕРИАЛЫ РЕВЬЮ ========`, затем Run, ревью, артефакт (вид, версия, заменяемая версия),
бриф, замечания QA (или «— нет»), кандидат. `fetch_decision` экспортирует Doc в текст и читает
**только блок до первого разделителя**.

| После `РЕШЕНИЕ:` (без пробелов по краям, без учёта регистра, финальные `.`/`!` отброшены) | Результат |
|---|---|
| пусто | `None` |
| `одобрено` / `approved` | `APPROVED` |
| `отклонено` / `rejected` | `REJECTED` |
| `доработать` / `changes_requested` | `CHANGES_REQUESTED` |
| иное | `None` + `WARNING` с `review_id` и значением |

`reason` — текст после `ПРИЧИНА:` и все следующие строки блока, обрезанный; пусто → `None`.

| Случай | `publish` | `fetch_decision` |
|---|---|---|
| Doc того же `review_id` с тем же пакетом | его место, без записи | — |
| Doc того же `review_id` с другим пакетом | `ReviewDeskError`, первый Doc остаётся | — |
| Doc нет (не публиковался / в корзине) | создаётся один Doc | `ReviewDeskError` |
| больше одного Doc на `review_id` | `ReviewDeskError` | `ReviewDeskError` |
| нет разделителя; нет `РЕШЕНИЕ:`/`ПРИЧИНА:` или строка повторена; `ПРИЧИНА:` выше `РЕШЕНИЕ:` | — | `ReviewDeskError` |
| не-2xx токена или Drive; сбой соединения/таймаут; ответ не JSON-объект или не той формы; id файла не `[A-Za-z0-9_-]+`; экспорт не UTF-8 | `ReviewDeskError` | `ReviewDeskError` |

Настройки — `google_docs_settings_from_env(environ)`: `OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE` (путь к
JSON-ключу) и `OMEMO_GOOGLE_REVIEW_FOLDER_ID` — обязательны; отсутствующие/пустые →
`ReviewDeskError` с их именами. Ключ читается сразу: нечитаем, не JSON-объект, `type` не
`service_account`, пустые `client_email`/`private_key`/`token_uri`, ключ не RSA PEM →
`ReviewDeskError` (путь назвать можно, ключ — никогда; в `repr` настроек ключа нет). Применение
решения (`fetch_decision` → `Run.submit_review`) — ниже, ADR-0045. Приёмка —
`ADAPTER_ACCEPTANCE.md` §10.

**Публикация ожидающего ревью (Этап 10, ADR-0044).** `application/review_publication.py`:

| Функция | Поведение |
|---|---|
| `pending_review_package(run) -> ReviewPackage \| None` | `None`, если Run не в `WAITING_HUMAN` или нет `PENDING` Review; иначе пакет последнего ожидающего Review: `candidate` — его Artifact, `brief` — `task_input` первой Task, `qa_flags` — флаги последней QA-оценки кандидата (`()` без флагов/оценки) |
| `publish_pending_review(run, desk) -> PublishedReview \| None` | `desk.publish(пакет)` → `PublishedReview(review_id, location)`; нечего публиковать → `None`, площадка не вызывается; `ReviewDeskError` пробрасывается |

Run только читается. Точка входа вызывает публикацию **после** возврата Director при каждом запуске:
повтор публикации того же `review_id` безвреден, поэтому это и первая попытка, и повтор после сбоя
или падения процесса; место в Run не сохраняется. `composition.build_review_desk(environ)` строит
`GoogleDocsReviewDesk` (fail closed, как `google_docs_settings_from_env`). `demo_notion.py`
публикует, если задана хотя бы одна переменная `OMEMO_GOOGLE_*` (неполная настройка — сообщение и
выход до вызова моделей), и печатает ссылку или отказ. Приёмка — `ADAPTER_ACCEPTANCE.md` §11.

**Применение решения ревьюера (Этап 10, ADR-0045).** `application/review_decision.py`
`apply_review_decision(run, desk) -> FetchedDecision | None`:

| Случай | Результат |
|---|---|
| Run не в `WAITING_HUMAN` или нет `PENDING` Review | `None`; площадка не вызывается |
| `fetch_decision(review_id)` последнего ожидающего Review → `None` | `None`; Run не изменён |
| `CHANGES_REQUESTED` / `REJECTED`; `APPROVED` при последней QA-оценке кандидата `PASSED` | `Run.submit_review(..., by=HUMAN_REVIEWER, reason=reason)`; `FetchedDecision(review_id, decision, applied=True)` |
| `APPROVED` при последней QA-оценке не `PASSED` (или без неё) | ничего не записано, Review остаётся `PENDING`; `applied=False` (ADR-0045 §3) |
| `ReviewDeskError` | пробрасывается; Run не изменён |

Run меняется только в памяти: сохраняет его и вызывает `resume` вызывающий.
`take_review_decision(store, desk, run_id) -> FetchedDecision | None` (ADR-0046) — эта первая половина
для сохранённого Run: нет Run → `None` без обращения к площадке; иначе публикует ожидающий Review
(идемпотентно), применяет решение и сохраняет **только** при `applied`; `ReviewDeskError`
пробрасывается, ничего не сохранено; `resume` остаётся за вызывающим. `demo_notion.py` при
настроенной площадке и без ручного флага решения вызывает её для сохранённого Run, затем продолжает
как прежде; отказ площадки печатается, запуск идёт дальше. Ручные `--approve` / `--request-changes` / `--reject` важнее площадки. Приёмка —
`ADAPTER_ACCEPTANCE.md` §12.

**Текст поста (ADR-0072).** `ReviewPackage.post` и `ReviewDecision.post` — необязательный `PostDraft(title, description)` (оба непустые, заголовок ≤ 100 символов): черновик показывается ревьюеру, решение несёт текст таким, каким его оставили. Google Docs-стол его не читает (`None`). У Notion-стола — два необязательных свойства `OMEMO_REVIEW_NOTION_POST_TITLE_PROPERTY` / `…_POST_DESCRIPTION_PROPERTY` (только парой): `publish` пишет в них черновик, `fetch_decision` читает; непостабельный текст → `None`; отпечаток включает черновик, только если он есть.

**Реализация на Notion (ADR-0060).** `infrastructure/notion_review_desk.py` `NotionReviewDesk` —
вторая реализация порта, рядом с `GoogleDocsReviewDesk`, ядро не меняется. Ревью — страница
**отдельной** базы; пакет пишется в блоки страницы, ответ ревьюера читается из **типизированных
свойств**, `url` страницы — это `location`, который возвращает порт. Транспорт — stdlib `urllib`,
`Notion-Version` закреплён; токен не появляется ни в `repr`, ни в сообщениях.

| Случай | Поведение |
|---|---|
| `publish` для нового `review_id` | создаётся страница: заголовок, `review_id` и отпечаток пакета в `rich_text`-свойствах, тело — инструкция, что ревьюится, исходный запрос, замечания QA, материал; возвращается `url` |
| `publish` того же `review_id` с тем же пакетом | вторая страница **не** создаётся, возвращается тот же `url` (идемпотентность порта; это же и повтор после сбоя, ADR-0044 §4) |
| `publish` того же `review_id` с другим пакетом | `ReviewDeskError`: ревьюер решал бы не то, что опубликовано (сверка по отпечатку — SHA-256 канонического JSON пакета) |
| текст длиннее 2000 символов; более 100 блоков | текст режется на куски по 2000, блоки сверх сотни дописываются отдельными запросами (ограничения Notion) |
| `fetch_decision`, свойство решения не выставлено | `None` — ревью ещё не завершено; это **единственная** оставшаяся неоднозначность |
| `fetch_decision`, выставлена одна из трёх опций | `Одобрено`/`Отклонено`/`Доработать` → `APPROVED`/`REJECTED`/`CHANGES_REQUESTED`; регистр и пробелы не важны, значения `ReviewStatus` тоже принимаются (та же терпимость, что в ADR-0043 §2) |
| причина | `rich_text`-свойство; пусто → `None` (контракт `ReviewDecision`) |
| опция вне трёх | `ReviewDeskError` — криво настроенная база, **не** молчание |
| `review_id` не публиковался | `ReviewDeskError` (требование порта) |
| две страницы с одним `review_id`; архивная страница | `ReviewDeskError`; архивная не считается опубликованной |
| нет свойства / неверный тип; не-2xx; сеть; не JSON | `ReviewDeskError` |

Поиск страницы — запрос к базе с фильтром по свойству `review_id`: тело `POST`, а не URL, поэтому
хешировать идентификатор (как ADR-0043 §3 делает для Drive) не нужно. Семь обязательных переменных
`OMEMO_REVIEW_NOTION_*` (токен, база, свойства заголовка, `review_id`, решения, причины, отпечатка),
без умолчаний для имён свойств — как в ADR-0040. Клип ревьюится по локальному пути в теле страницы:
`ArtifactView.content` — это текст, и Notion не проиграет файл с машины мейнтейнера (ADR-0060 §4).
Приёмка — `ADAPTER_ACCEPTANCE.md` §14.

**Проводка** (выбор площадки в Composition Root и `demo_notion.py`) — следующая подзадача, как
`build_brief_board` последовал за ADR-0040.

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
