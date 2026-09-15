# EVALUATION_SPEC.md — Спецификация сущности Evaluation (QA) и гейта fail closed

> Технический контракт по `ADR-0018`. Подчинён `DOMAIN_MODEL.md` §2.13/§6 и `PROJECT.md` §10/§12
> (PROJECT §17: при конфликте побеждают документы). Приёмка — `EVALUATION_ACCEPTANCE.md`.
>
> **Статус:** Accepted. **Дата:** 2026-09-15.

---

## 1. Назначение и границы

- **Что это.** Evaluation — структурированное суждение «пройдено / риск / отклонить» о
  артефакте-кандидате с перечнем замечаний/флагов. Частный случай — QA-вердикт домена здоровья,
  работающий по принципу `fail closed`.
- **Зачем.** Сделать инвариант «при риске контент дальше не идёт» (DOMAIN_MODEL §6) исполняемым:
  без вердикта `PASSED` артефакт нельзя одобрить, а значит — опубликовать.
- **В scope.** Доменная сущность, её жизненный цикл через Run, гейт одобрения артефакта, порт
  QA-оценщика в прикладном слое, маршрутизация по вердикту в `ContentDirector`.
- **Вне scope этого документа.** Реальный QA Agent (промпт, схема вердикта, правила комплаенса —
  ROADMAP Этап 8); оценка Output; оценка уверенности; ссылка на критерии; метка времени.
  Доработка после эскалации риска и `CHANGES_REQUESTED` реализуется отдельным прикладным
  контрактом `REWORK_ROUTING_SPEC.md` / ADR-0032.

## 2. Сущность

| Атрибут | Тип | Изменяемость |
|---|---|---|
| `evaluation_id` | `EvaluationId` (`str`, генерирует Run: `<run_id>-evaluation-<n>`) | неизменяем |
| `run_id` | `str` (владеющий Run) | неизменяем |
| `artifact_ref` | `str` (id артефакта-кандидата) | неизменяем |
| `kind` | `str` (вид оценки, напр. `"qa"`) | неизменяем |
| `status` | `EvaluationStatus` (вердикт) | один раз: `PENDING` → терминальный |
| `flags` | `tuple[str, ...]` (замечания / флаги риска) | один раз, вместе с вердиктом |

Наружу выходит только неизменяемый снимок `EvaluationView` с теми же полями.

## 3. Жизненный цикл

```
PENDING ──► PASSED    (пройдено)
        ├─► FLAGGED   (риск)
        └─► FAILED    (отклонить)
```

- Создаётся `PENDING`; вердикт выносится **ровно один раз**; все три вердикта терминальны.
- `PENDING` не является вердиктом. Повторная оценка — **новая** Evaluation того же артефакта.

## 4. Контракт через Run (единственный путь)

| Операция | Кто | Предусловия | Эффект |
|---|---|---|---|
| `open_evaluation(artifact_id, *, kind, by)` | Content Director | артефакт `CANDIDATE` | новая `PENDING` Evaluation; событие не порождается |
| `record_evaluation(evaluation_id, verdict, *, by, flags=())` | Content Director | Evaluation `PENDING`; `verdict` ∈ {PASSED, FLAGGED, FAILED} | вердикт и флаги фиксируются; `EvaluationCompleted` |
| `evaluations` / `evaluation(id)` | чтение | — | снимки `EvaluationView` |

Вердикт выносит оценщик вне домена; Run его только **сохраняет** (Variant A, ADR-0013 §8).
`AGENT` и `HUMAN_REVIEWER` не могут ни открыть, ни записать оценку.

## 5. Гейт fail closed

Переход артефакта `CANDIDATE → APPROVED` разрешён, только если **оба** условия выполнены:

1. есть `APPROVED` Human Review этого артефакта (ADR-0007; проверяется первым →
   `ArtifactNotApprovedError`);
2. **последняя** открытая Evaluation этого артефакта имеет вердикт `PASSED`
   (→ иначе `ArtifactQaNotPassedError`).

| Состояние QA артефакта | Одобрение |
|---|---|
| оценок нет | запрещено |
| последняя `PENDING` | запрещено |
| последняя `FLAGGED` / `FAILED` | запрещено (даже при Approve человека) |
| последняя `PASSED` | разрешено |

- Оценки других артефактов не учитываются.
- `CANDIDATE → REJECTED` не требует ни ревью, ни оценки.
- Машина состояний Run (`Run.transition`) не меняется.

## 6. События

`EvaluationCompleted(run_id, evaluation_id, artifact_ref, verdict, flags)` — в единый журнал Run.

## 7. Ошибки

| Ошибка | База | Когда |
|---|---|---|
| `InvalidEvaluationTransitionError` | `EvaluationDomainError` | повторный вердикт; `PENDING` как вердикт |
| `ImmutableEvaluationAttributeError` | `EvaluationDomainError` | запись в неизменяемый атрибут |
| `ArtifactQaNotPassedError` | `ArtifactDomainError` | одобрение без последнего `PASSED` |
| `InvalidTransitionError` (Run) | `RunDomainError` | оценка не-`CANDIDATE` артефакта |
| `UnauthorizedActorError` (Run) | `RunDomainError` | не Content Director |

`EvaluationDomainError` наследует `DomainError` (ADR-0017).

## 8. Прикладной слой — порт QA

`application/qa_evaluation.py`:

- `EvaluationResult(verdict: EvaluationStatus, flags: tuple[str, ...] = ())`;
- `ArtifactEvaluator` — протокол `evaluate(content: str) -> EvaluationResult`;
- `evaluate_artifact(run, evaluator, artifact_id, *, kind="qa") -> EvaluationId` — открывает
  оценку, передаёт оценщику содержимое артефакта, записывает вердикт.

Исключения оценщика **не перехватываются** (PROJECT §10): Evaluation остаётся `PENDING`, гейт
закрыт. Вердикта по умолчанию нет.

## 9. Маршрутизация в ContentDirector

`ContentDirector(executor, schemas=None, qa=None)`. Без `qa` поведение прежнее. С `qa`, после
успеха всех задач и перехода Run в `WAITING_QA`:

| Ситуация | Маршрут |
|---|---|
| финальный шаг не дал артефакта | Run → `FAILED`, причина `QA gate: no candidate artifact to evaluate` |
| вердикт `PASSED` | артефакт финального шага `CANDIDATE`; далее прежний маршрут `WAITING_HUMAN` → `COMPLETED` |
| вердикт `FLAGGED` / `FAILED` | Run → `WAITING_HUMAN`, открыт Human Review кандидата; Run **не** завершается |

Оценивается только артефакт финального шага; промежуточные остаются `DRAFT`.
Если человек отвечает `CHANGES_REQUESTED`, дальнейший маршрут определён
`REWORK_ROUTING_SPEC.md`: повторно исполняется producer текущего кандидата, а его Output создаёт
новую версию Artifact. Сам QA-риск до решения человека по-прежнему только эскалируется.
