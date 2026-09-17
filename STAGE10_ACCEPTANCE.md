# STAGE10_ACCEPTANCE.md — Acceptance Criteria (ROADMAP Этап 10, интеграция с Google Docs)

**Версия:** 1.0
**Статус:** Принят
**Дата:** 2026-09-17
**Префикс тестов:** `S10A`
**Решение:** `docs/adr/0046-stage-10-acceptance-and-review-decision-intake.md`

Производственный путь `STAGE9_ACCEPTANCE.md` (встроенный каталог Prompt для трёх ролей, Skill и Tool
Rin, настоящие `AnthropicLLMClient` с подменённым транспортом, настоящий `SqliteRunStore` под
`BriefStatusReporter` над `InMemoryBriefBoard`) плюс площадка ревью — `InMemoryReviewDesk` в роли
Google Doc: строку решения можно перепечатать, площадку можно «уронить». Каждый запуск — новый
процесс, делающий то же, что `demo_notion.py`: `take_review_decision` → `produce_brief` →
`publish_pending_review`.

Definition of Done Этапа 10 (ROADMAP.md): (1) кандидат публикуется с контекстом (бриф, флаги QA,
версии); (2) решение человека (Approve/Reject/Request changes) считывается и доводится до
оркестратора.

## Acceptance

| ID | Критерий |
|---|---|
| S10A-01 | QA `passed` / `flagged` → Run `WAITING_HUMAN`; в конце запуска опубликован ровно пакет: этот Run, его единственный Review, кандидат — версия 1 без `supersedes_ref` в `CANDIDATE`, бриф — текст брифа, флаги — последней QA (пусто при `passed`); место `memory://reviews/<review_id>`; сохранённый Run равен Run. |
| S10A-02 | Решения нет → следующий запуск не вызывает моделей, решения нет, то же место, снимок не изменён, доска без новых статусов. |
| S10A-03 | На площадке «одобрено» → следующий запуск без моделей: `FetchedDecision(applied=True)`, Review `APPROVED` от `human_reviewer`, артефакт `APPROVED`, Run `COMPLETED`, публиковать нечего, на доске добавлен `completed`. |
| S10A-04 | QA `flagged`; «доработать» / «отклонено» с причиной → следующий запуск вызывает только Leo (его Prompt) и QA; вход доработки несёт `human_decision` и причину; Review v1 решён с причиной; v1 `SUPERSEDED`; опубликован новый Review: версия 2 с `supersedes_ref` v1, без флагов, с исходным брифом; доска `…waiting_human, running, waiting_qa, waiting_human`. Одобрение v2 → `COMPLETED` без моделей. |
| S10A-05 | QA `flagged`, на площадке «одобрено» → запуск без моделей, `applied=False`, снимок не изменён, Review `PENDING` (опубликован тот же). Ревьюер перепечатывает «доработать» → следующий запуск дорабатывает, версия 2 ждёт человека. |
| S10A-06 | Публикация отказана (площадка лежит) → `ReviewDeskError`, Run в store `WAITING_HUMAN` не изменён, ничего не опубликовано. Площадка поднята → `take_review_decision` следующего запуска публикует без моделей; одобрение затем завершает Run. |
| S10A-07 | Решение есть, площадка лежит при чтении → `ReviewDeskError`, сохранённый Run не изменён, моделей и новых статусов нет. Площадка поднята → следующий запуск применяет решение, Run `COMPLETED`. |
| S10A-08 | Процесс падает после сохранения решения, до `resume` → следующий запуск не обращается к площадке, не вызывает моделей и завершает Run; на доске ровно `…waiting_human, completed`. |
| S10A-09 | Ревьюер отклоняет каждую версию: `max_rework_iterations` доработок проходят, следующий отказ → Run `FAILED` с `REWORK_LIMIT_REASON` без вызова моделей; публиковать нечего; последний статус на доске — `failed`. |

## Вне критериев

Живой обмен с Google Docs — проверка оператора через `demo_notion.py` (нужны сервисный аккаунт и
общая папка); разбор Drive API и строки `РЕШЕНИЕ:` — `ADAPTER_ACCEPTANCE.md` §10 (`GDR`); публикация
и применение решения по отдельности — §11 (`RPB`), §12 (`RDF`); маршруты Approval Gate —
`EVALUATION_ACCEPTANCE.md` §4.5 (`APG`).

## Quality gate

Все S10A-тесты и полный gate (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`) должны
быть зелёными до коммита.
