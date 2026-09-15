# M2_ACCEPTANCE.md — Acceptance Criteria (ROADMAP Этап 7, Milestone M2)

**Версия:** 1.0
**Статус:** Принят
**Дата:** 2026-09-15
**Префикс тестов:** `M2A`
**Решение:** `docs/adr/0033-invalid-output-contract-error-and-m2-acceptance.md`

Один проход Workflow `research-to-script@v1` (Rin → Leo) через `compile_runtime` только с
production-активами: встроенный каталог Prompt (`prompts=None`), Skill-вызов Rin, Tool Rin,
настоящий `AnthropicLLMClient` и настоящий `SqliteRunStore`. Подменён только сетевой транспорт под
Anthropic SDK (скриптованный `messages.create`, возвращающий настоящие SDK `Message`).

## Acceptance

| ID | Критерий |
|---|---|
| M2A-01 | Проход завершает Run в `COMPLETED`: две Task `SUCCEEDED`, два `VALID` Output с Schema refs каталога, по одному Artifact на Output; Output Rin — вход Task Leo. |
| M2A-02 | Провайдер получает System/User текст из встроенного каталога Prompt; каждая Analytics Record несёт точный `<prompt_id>@v<version>`. |
| M2A-03 | Skill применяется до вызова модели: провайдер видит нормализованный бриф, а Task хранит исходный. |
| M2A-04 | Tool вызывается посреди рассуждения: Rin получает `current_date` и его результат (с внедрённой датой) во втором ходе; Leo без grant не видит операционных Tools. |
| M2A-05 | Каждый завершённый ход провайдера — одна Analytics Record: provider, фактическая model, токены, точная `Decimal` стоимость, время, `retries = 0`, Task и роль. |
| M2A-06 | SQLite-файл, прочитанный новым store, даёт снимок, равный Run; `resume` завершённого Run не вызывает модель. |
| M2A-07 | `INVALID` ответ модели — ошибка контракта: Output записан `INVALID`, Artifact не создан, следующая Task не открыта, модель не вызвана снова, Run `FAILED` с `INVALID_OUTPUT_REASON`, метрики вызова записаны. |
| M2A-08 | Run, восстановленный между записью `INVALID` Output и переходом в `FAILED`, при `resume` уходит в `FAILED` без повторного вызова. |
| M2A-09 | `INVALID` Output rework-итерации не создаёт версию: кандидат остаётся живым, Run `FAILED` с `REWORK_NO_OUTPUT_REASON`. |

## Вне критериев

Живой вызов провайдера — проверка оператора через `demo_factory.py` (нужны ключ и цены), не quality
gate (ADR-0033 §4).

## Quality gate

Все M2A-тесты и полный gate (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`) должны
быть зелёными до коммита.
