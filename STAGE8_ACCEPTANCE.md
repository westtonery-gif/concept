# STAGE8_ACCEPTANCE.md — Acceptance Criteria (ROADMAP Этап 8, QA Agent)

**Версия:** 1.0
**Статус:** Принят
**Дата:** 2026-09-17
**Префикс тестов:** `S8A`
**Решение:** `docs/adr/0039-stage-8-acceptance.md`

Проход Workflow `research-to-script@v1` (Rin → Leo) с QA-гейтом `qa_agent@v1` через
`compile_runtime(..., qa=build_qa_evaluator(...))` только с production-активами: встроенный каталог
Prompt (`prompts=None`) для всех трёх ролей, Skill и Tool Rin, настоящий `AnthropicLLMClient` —
**отдельный** для producer-ролей и для QA-роли, со своими ценами — и настоящий `SqliteRunStore`.
Подменён только сетевой транспорт под Anthropic SDK (скриптованный `messages.create`, возвращающий
настоящие SDK `Message`). Каждый перезапуск — новый процесс: новый Composition Root над тем же
SQLite-файлом.

Definition of Done Этапа 8 (ROADMAP.md): (1) QA выдаёт структурированный вердикт; при риске система
не пропускает контент дальше; (2) оркестратор корректно маршрутизирует по вердикту QA.

## Acceptance

| ID | Критерий |
|---|---|
| S8A-01 | Модель QA отвечает `passed`: Run `COMPLETED`; ровно одна Evaluation — `PASSED`, `evaluator_ref = qa_agent@v1`, на финальном Artifact (скрипт Leo, `CANDIDATE`); промежуточный Artifact Rin `DRAFT` и не оценён; Human Review не открыт. |
| S8A-02 | Провайдер QA получает System и User из записи `qa-agent` встроенного каталога (User = шаблон с содержимым скрипта Leo), принудительный `emit_fields` ровно с полями `verdict`/`flags` и без операционных Tools. |
| S8A-03 | Вызов QA — одна Analytics Record, привязанная к Evaluation (`task_id` пуст, `evaluation_id`, `agent_ref = qa_agent@v1`, `prompt_ref = qa-agent@v<n>`, фактическая model QA, точная стоимость по ценам QA-роли, `retries = None`); записи producer-ролей не изменились. |
| S8A-04 | Модель QA отвечает `flagged` или `failed` с флагами: Run `WAITING_HUMAN`, не `COMPLETED`; Evaluation с вердиктом и флагами модели; открыт `PENDING` Human Review на кандидате; сохранённая строка равна Run. Даже `APPROVED` человека не одобряет Artifact (`ArtifactQaNotPassedError`), а `resume` без решения человека не вызывает модель. |
| S8A-05 | Риск → человек `CHANGES_REQUESTED` → перезапуск `resume`: вызывается только Leo (Rin — нет), его вход — канонический JSON с флагами модели QA и инструкцией человека; результат — версия 2 скрипта, версия 1 `SUPERSEDED`; QA заново оценивает именно версию 2; при `passed` Run `WAITING_HUMAN` со свежим `PENDING` Review версии 2; `APPROVED` человека теперь одобряет версию 2. |
| S8A-06 | Модель QA нарушает грамматику вердикта: `QaVerdictError` пробрасывается; сохранённый Run `WAITING_QA`, Evaluation `PENDING`, вызов записан. Перезапуск `resume` спрашивает QA снова на той же Evaluation, producer-роли не вызываются, Run `COMPLETED`, обе записи вызовов QA на этой Evaluation. |
| S8A-07 | Завершённый Run, прочитанный новым store, равен Run; его `resume` не вызывает ни producer-модель, ни модель QA. |

## Вне критериев

Качество реального вердикта и формулировки критериев (`qa-agent` v1 ещё говорит о здоровье — задача
12, v2); живой вызов провайдера — проверка оператора через `demo_factory.py` (ADR-0033 §4,
ADR-0038 §4); маршрутизация решений человека `APPROVED` / `REJECTED` в состояние Run (Этап 10).

## Quality gate

Все S8A-тесты и полный gate (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`) должны
быть зелёными до коммита.
