# SKILL_SPEC.md — Спецификация Skill и библиотеки Skills

> Технический контракт по `ADR-0021`. Подчинён `PROJECT.md` §14/§18, `ARCHITECTURE.md` §7 и
> `DOMAIN_MODEL.md` §2.6/§9.5 (PROJECT §17: при конфликте побеждают документы). Приёмка —
> `SKILL_ACCEPTANCE.md`.
>
> **Статус:** Accepted. **Дата:** 2026-09-15.

---

## 1. Назначение и границы

- **Что это.** Skill — переиспользуемая детерминированная операция **одной задачи**, из которых
  агенты собирают поведение. Библиотека Skills — пакет `omemo_content_factory.skills`.
- **В scope.** Дескриптор каталога, исполняемый контракт `Skill`, три первых Skill, каталог,
  ошибки, проверки границ.
- **Вне scope.** Ссылки Agent → Skill (Этап 7); статус `Active`/`Deprecated`; Skills, опирающиеся
  на Tools/Adapters (после Этапов 5–6); реестр с разрешением по ref в рантайме; LLM внутри Skill.

## 2. Дескриптор (`domain/skill.py`)

| Атрибут | Тип | Правила (иначе `InvalidSkillDescriptorError`) |
|---|---|---|
| `skill_id` | `SkillId` (`str`) | непустой, без пробелов и `@` |
| `version` | `SkillVersion(value: int)` | `int ≥ 1`; `bool` не принимается |
| `name` | `str` | непустой |
| `purpose` | `str` | непустой; формулирует одну задачу |
| `ref` (производное) | `SkillRef` (`str`) | `<skill_id>@v<n>` |

Неизменяем (frozen dataclass). Входной/выходной контракт — типизированная сигнатура `apply`, а не
поле дескриптора.

## 3. Исполняемый контракт (`skills/contract.py`)

`Skill[InT, OutT]` — структурный `Protocol`: `descriptor -> SkillDescriptor` и
`apply(skill_input: InT, /) -> OutT`. Правила для каждого Skill:

1. Вход — frozen dataclass, проверяемый при создании (`InvalidSkillInputError`); выход — frozen
   dataclass.
2. `apply` чистая: одинаковый вход → равный выход; без часов, случайности, I/O, сети, модели и
   состояния между вызовами; класс Skill без состояния (`__slots__ = ()`).
3. `apply` принимает **только** свой вход — ни `Run`, ни `Task`, ни агента, ни актора.
4. Модули библиотеки импортируют только чистый stdlib, `domain.skill` и друг друга.

## 4. Skills v1

| Ref | Вход | Выход | Поведение |
|---|---|---|---|
| `segment_text@v1` | `SegmentTextInput(text: str, max_chars: int ≥ 1)` | `SegmentTextOutput(segments: tuple[str, ...])` | абзацы (разделённые пустой строкой) не сливаются; целые предложения (конец — `.`/`!`/`?`/`…`) упаковываются жадно в ≤ `max_chars`; слишком длинное предложение — по словам, слишком длинное слово — нарезается. Пробелы нормализуются; все непробельные символы сохраняются по порядку; пустой текст → `()` |
| `normalize_terminology@v1` | `NormalizeTerminologyInput(text: str, glossary: tuple[TermMapping(variant, canonical), ...])` | `NormalizedText(text, replacements: tuple[TermReplacement(variant, canonical, occurrences), ...])` | целые слова, без учёта регистра и вида пробелов; один проход, длинные варианты первыми (канон не заменяется повторно); уже каноничные вхождения не считаются; отчёт — только ненулевые, в порядке глоссария. Глоссарий: непустые `variant`/`canonical`, варианты уникальны без учёта регистра/пробелов; пустой глоссарий — текст без изменений |
| `check_required_elements@v1` | `CheckRequiredElementsInput(text: str, elements: tuple[RequiredElement(element_id, phrases), ...])` | `RequiredElementsReport(present, missing)` + `complete` | элемент присутствует, если найдена любая его фраза (целые слова, без учёта регистра/пробелов, фраза — литерал, не regex). Только отчёт — решения принимают QA/Content Director. Набор элементов непустой; `element_id` непустые и уникальные; у элемента ≥ 1 непустой фразы |

## 5. Каталог (`skills/catalogue.py`)

`SKILL_DESCRIPTORS` (кортеж) и `SKILLS_BY_REF` (`ref → дескриптор`); ref уникальны. Пассивные
данные: в рантайме ничего не разрешается по ref — агент использует Skill импортом.

## 6. Ошибки

| Ошибка | База | Когда |
|---|---|---|
| `InvalidSkillDescriptorError` | `SkillDomainError` | некорректные id/имя/назначение/версия |
| `InvalidSkillInputError` | `SkillDomainError` | вход нарушает контракт Skill |
| `dataclasses.FrozenInstanceError` | — | попытка изменить дескриптор, вход или выход |

`SkillDomainError` наследует `DomainError` (ADR-0017).
