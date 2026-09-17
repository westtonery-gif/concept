# STAGE9_ACCEPTANCE.md — Acceptance Criteria (ROADMAP Этап 9, интеграция с Notion)

**Версия:** 1.0
**Статус:** Принят
**Дата:** 2026-09-17
**Префикс тестов:** `S9A`
**Решение:** `docs/adr/0042-stage-9-acceptance-and-brief-intake.md`

Бриф с доски проходит Workflow `research-to-script@v1` (Rin → Leo) с QA-гейтом `qa_agent@v1` только
через `application.brief_intake.produce_brief`. Активы — как в `STAGE8_ACCEPTANCE.md`: встроенный
каталог Prompt для трёх ролей, Skill и Tool Rin, настоящий `AnthropicLLMClient` отдельно для
producer-ролей и для QA (подменён только транспорт под SDK), настоящий `SqliteRunStore`. Доска —
`InMemoryBriefBoard` (фейк, названный в DoD), обёрнутая в `BriefStatusReporter`, через который
коммитит Director. Каждый перезапуск — новый Composition Root, новый репортёр над тем же SQLite-файлом
и той же доской.

Definition of Done Этапа 9 (ROADMAP.md): (1) бриф из Notion порождает валидный `Run`; статусы
возвращаются в Notion; (2) ядро не знает специфики Notion вне адаптера; контракт покрыт тестами
против фейка.

## Acceptance

| ID | Критерий |
|---|---|
| S9A-01 | Готовый бриф на доске → `produce_brief` возвращает Run: `run_id` — переданный, `content_brief_ref` — ссылка брифа, `workflow_version_ref` — id Workflow; вход первой Task — ровно текст брифа, и он есть в User первого вызова Rin; при `passed` Run `COMPLETED`, сохранённая строка равна Run. |
| S9A-02 | На доске под брифом — ровно `queued`, `running`, `waiting_qa`, `waiting_human`, `completed`, каждый один раз, с `run_id` Run; `shown_status` равен статусу Run; отказанных отчётов нет. |
| S9A-03 | Бриф неизвестен доске или не готов → `None`; Run не сохранён, ни одна модель не вызвана, отчётов нет. |
| S9A-04 | Риск QA → доска показывает `waiting_human`; человек `CHANGES_REQUESTED` (зафиксирован в store) → перезапуск `produce_brief` вызывает только Leo, v2 оценивается снова; на доске после прежних отчётов добавлены `running`, `waiting_qa`, `waiting_human`. |
| S9A-05 | Процесс падает сразу после коммита `queued` (до первой Task) → перезапуск `produce_brief`: вход первой Task — текст брифа с доски, не пустая строка; Run `COMPLETED`, доска показывает `completed`. Если к перезапуску бриф снят с готовности — `BriefIntakeError`, сохранённый Run не изменён, модели не вызваны, новых отчётов нет. |
| S9A-06 | Модель QA нарушает грамматику вердикта → `QaVerdictError` пробрасывается, доска показывает `waiting_qa` (Run сохранён в нём); перезапуск спрашивает QA снова, producer-роли не вызываются, доска показывает `completed`. |
| S9A-07 | Доска отказывает во всех отчётах (`BriefBoardError`) → прогон всё равно `COMPLETED` с тем же снимком, что при доступной доске; отказы в `failed_reports`. Доска снова доступна → следующий `produce_brief` не вызывает моделей и показывает `completed`. |
| S9A-08 | Сохранённый Run под `run_id` принадлежит другому брифу → `BriefIntakeError`; Run не изменён, моделей и отчётов нет. |

## Вне критериев

Живой обмен с Notion — проверка оператора через `demo_notion.py` (нужны токен интеграции и база);
разбор HTTP API Notion — `ADAPTER_ACCEPTANCE.md` §8 (`NBB`); семантика репортёра по отдельности —
§9 (`BSR`); граница «ядро не импортирует сеть/сторонние пакеты» — `tests/test_adapter_contract.py`.

## Quality gate

Все S9A-тесты и полный gate (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`) должны
быть зелёными до коммита.
