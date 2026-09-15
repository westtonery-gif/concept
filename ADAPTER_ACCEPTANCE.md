# ADAPTER_ACCEPTANCE.md — Критерии приёмки контрактов слоя адаптеров

> Приёмка `ADAPTER_SPEC.md` (по `ADR-0023`). Каждый критерий фальсифицируем: при неверной
> реализации соответствующий тест падает. Идентификаторы используются в именах тестов
> (`tests/test_adapter_contract.py`, `tests/test_domain_error.py`).
>
> **Статус:** Accepted. **Дата:** 2026-09-15.

---

## 0. Соглашения

- Проверяются контракты, а не реализации: реализаций на этом шаге нет. Соответствие протоколам
  доказывается mypy --strict на минимальных тестовых конформерах.
- Проверки границ читают исходники пакета (`ast`), а не поведение.

## 1. Контракты (ADC) — `tests/test_adapter_contract.py`

| ID | Сценарий | Ожидание |
|---|---|---|
| ADC-01 | Форма каждого протокола | ровно объявленные публичные методы; имена и виды параметров как в спеке (одиночный аргумент — positional-only; `run_id`/`status` у `report_status` — keyword-only); тестовые конформеры проходят mypy --strict |
| ADC-02 | `IncomingBrief`: пустой/пробельный/не-строковый `brief_ref` или `body` | `ValueError`; корректный — неизменяем |
| ADC-03 | `ReviewPackage`: пустые `run_id`/`review_id`/`brief`; кандидат другого Run; кандидат не `CANDIDATE`; `qa_flags` не кортеж / с пустым флагом | `ValueError`; корректный (с флагами и без) — неизменяем |
| ADC-04 | `ReviewDecision`: `PENDING`; строка вместо `ReviewStatus`; пустая причина | `ValueError`; три терминальных решения принимаются; неизменяем |
| ADC-05 | Контракты и неизменённый API Run | Run создаётся из `IncomingBrief`, сохраняется в `RunStore`, его статус — в `BriefBoard`, его `analytics_records` — в `AnalyticsSink`; `ReviewDecision` передаётся в `Run.submit_review` без преобразований (mypy) |

## 2. Границы (ADB) — `tests/test_adapter_contract.py`

| ID | Сценарий | Ожидание |
|---|---|---|
| ADB-01 | Импорты модулей `adapters/` | только разрешённый чистый stdlib, `domain.*`, `adapters.*` |
| ADB-02 | Импорты всех модулей вне `infrastructure/` | нет сторонних пакетов и сетевых/файловых модулей stdlib — ядро не ходит наружу напрямую |
| ADB-03 | Кто импортирует `infrastructure` | только Composition Root |
| ADB-04 | Импорты `domain/` | только stdlib и `domain.*` — домен не знает об адаптерах |
| ADB-05 | Идентификаторы в `adapters/` | нет имён поставщиков (`notion`, `google`, `gdoc`, `anthropic`, `claude`, `n8n`, `sql`) |
| ADB-06 | Модули пакета | все просканированы и импортируются по отдельности |

## 3. Ошибки (ADT) — `tests/test_domain_error.py`

| ID | Сценарий | Ожидание |
|---|---|---|
| ADT-01 | `RunStoreError`, `BriefBoardError`, `ReviewDeskError`, `AnalyticsSinkError` | технические — **не** `DomainError` |

## 4. Что приёмка НЕ проверяет

Поведение реализаций — атомарность и идемпотентность `save`, `None` для неизвестного `run_id`,
проброс `RunRestorationError`, идемпотентность `publish`/`report_status`/`export` (проверяются
против каждой реализации с 6b/6c); проводку адаптеров в Content Director; реальные Notion/Google
Docs/аналитику (Этапы 9–10, 13–14).
