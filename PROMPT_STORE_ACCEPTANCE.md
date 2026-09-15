# PROMPT_STORE_ACCEPTANCE.md — Acceptance Criteria

**Версия:** 1.1 (PST-01, PST-02: QA-роль, ADR-0035)
**Статус:** Принят
**Дата:** 2026-09-16
**Префикс тестов:** `PST`

## Acceptance

| ID | Критерий |
|---|---|
| PST-01 | Встроенный каталог содержит ровно Prompt Rin, Leo и QA-роли; Rin и Leo загружаются с прежними id, версиями, Schema refs и точным текстом (текст QA-роли проверяет QAR-03). |
| PST-02 | Production role-модули не конструируют `Prompt` и не содержат System/User template. |
| PST-03 | `build_executor_map(..., prompts=None, ...)` читает встроенный каталог и передаёт его текст и точный version ref в executor. |
| PST-04 | Верхнеуровневая сборка с `prompts=None` использует один загруженный снимок для executor/schema maps и не вызывает модель на build-time. |
| PST-05 | Явная mapping Prompt продолжает работать без чтения встроенного ресурса. |
| PST-06 | Нечитаемый или синтаксически неверный TOML даёт `CompositionError`. |
| PST-07 | Неверный корень, пустой каталог, не-table запись, отсутствующее/лишнее поле дают `CompositionError`. |
| PST-08 | Пустая строка, неверный тип, `bool`/неположительная версия дают `CompositionError`. |
| PST-09 | Повторный `prompt_id` даёт `CompositionError`; частичный результат не наблюдаем. |
| PST-10 | Wheel содержит TOML-каталог Prompt как пакетный ресурс. |

## Quality gate

Все PST-тесты и полный gate (`ruff check`, `ruff format --check`, `mypy --strict`, `pytest`) должны
быть зелёными до коммита.
