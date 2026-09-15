# TOOL_SPEC.md — Спецификация Tool и слоя Tools

> Технический контракт по `ADR-0022`. Подчинён `PROJECT.md` §18, `ARCHITECTURE.md` §3.7/§8/§15 и
> `DOMAIN_MODEL.md` §2.5/§6/§9.3 (PROJECT §17: при конфликте побеждают документы). Приёмка —
> `TOOL_ACCEPTANCE.md`.
>
> **Статус:** Accepted. **Дата:** 2026-09-15.

---

## 1. Назначение и границы

- **Что это.** Tool — операционная возможность, которую **модель** агента вызывает во время
  рассуждения, чтобы получить данные или выполнить действие. Слой Tools — пакет
  `omemo_content_factory.tools`.
- **В scope.** Дескриптор, исполняемый контракт `Tool`, `ToolCall`/`ToolResult`, `Toolbox`
  (агенту доступны только выданные Tools), выдача `Agent.tool_refs`, два первых Tool, каталог,
  ошибки, проверки границ.
- **Вне scope.** Цикл рассуждения (tool-use loop) в LLM-адаптере и сборка Toolbox в composition
  root (Этап 7); Tools поверх адаптеров (после Этапа 6); запись вызовов в трассировку/аналитику
  (Этап 14); статус `Active`/`Deprecated`.

## 2. Дескриптор (`domain/tool.py`)

| Атрибут | Тип | Правила (иначе `InvalidToolDescriptorError`) |
|---|---|---|
| `tool_id` | `ToolId` (`str`) | `[a-z][a-z0-9_]{0,63}`; это же имя, по которому Tool вызывает модель |
| `version` | `ToolVersion(value: int)` | `int ≥ 1`; `bool` не принимается |
| `description` | `str` | непустой; модель читает его, чтобы решить, когда вызывать Tool |
| `parameters` | `tuple[ToolParameter, ...]` | кортеж; имена уникальны; может быть пустым |
| `ref` (производное) | `ToolRef` (`str`) | `<tool_id>@v<n>` |

`ToolParameter(name, kind, description, required=True)`: `name` — тот же шаблон, что `tool_id`;
`kind` — `ToolParameterType` (`string` / `integer` / `boolean`); `description` непустой;
`required` — `bool`. Всё неизменяемо (frozen dataclass).

В отличие от Skill, параметры — данные дескриптора: у них два читателя в рантайме (модель и
`Toolbox`), и ни один не читает Python-сигнатуру (ADR-0022 §2).

## 3. Исполняемый контракт (`tools/contract.py`)

`Tool` — структурный `Protocol`: `descriptor -> ToolDescriptor` и
`invoke(arguments: ToolArguments, /) -> Mapping[str, ToolValue]`, где `ToolValue = str | int | bool`,
`ToolArguments = Mapping[str, ToolValue]`. Правила для каждого Tool:

1. `invoke` принимает **только** проверенные аргументы — ни `Run`, ни `Task`, ни агента, ни актора.
2. Нет состояния между вызовами. Внешняя зависимость (часы; после Этапа 6 — адаптер) только
   **внедряется при создании**; Tool не читает системные часы (`now`/`today`/`utcnow`).
3. Модули слоя импортируют только разрешённый чистый stdlib, `domain.tool` и друг друга — не
   `skills` (Skills могут зависеть от Tools, ARCHITECTURE §3.6), не агентов, application,
   infrastructure, SDK, сеть, ФС, `time`, `random`.
4. Отказ — `ToolExecutionError` (техническая ошибка, **не** `DomainError`).

`ToolCall(name, arguments)` — сырые данные модели; при создании не проверяются.
`ToolResult(status, data, error)`:

| `status` | Смысл | `data` | `error` |
|---|---|---|---|
| `OK` | Tool отработал | данные Tool (read-only) | `""` |
| `REFUSED` | вызов отклонён, Tool **не запускался** | пусто | причина, непустая |
| `FAILED` | Tool запустился и сообщил `ToolExecutionError` | пусто | причина, непустая |

Несогласованное сочетание полей → `ValueError`.

| | Skill | Tool |
|---|---|---|
| Кто решает вызвать | код агента/системы | модель во время рассуждения |
| Как достигается | импорт и прямой вызов | только через `Toolbox` агента |
| Вход | типизированный frozen dataclass | недоверенный JSON модели, проверяется по параметрам |
| Детерминизм | обязателен | не обязателен; внешний мир — только внедрённый |
| Глагол | `apply` | `invoke` |

## 4. `Toolbox` (`tools/toolbox.py`)

`Toolbox(grants=agent.tool_refs, available=<экземпляры Tool>)` — один на агента.

**Создание** (иначе `ToolGrantError`): `grants` — не одна строка; каждый ref есть среди
`available`; ref не повторяется; не выданы две версии одного `tool_id`; у двух `available` нет
общего ref.

**`descriptors`** — только выданные Tools, в порядке `grants` (это видит модель).

**`invoke(call) -> ToolResult`** — единственный путь от вызова модели к Tool; из-за данных модели
не бросает никогда:

| Ситуация | Результат | Tool запущен |
|---|---|---|
| `name` не строка / не выданный Tool | `REFUSED` (перечень выданных) | нет |
| `arguments` не mapping | `REFUSED` | нет |
| необъявленный аргумент | `REFUSED` | нет |
| нет обязательного аргумента | `REFUSED` | нет |
| значение не того вида (`True` — не `integer`) | `REFUSED` | нет |
| Tool бросил `ToolExecutionError` | `FAILED` | да |
| иначе | `OK` + данные | да |

Tool получает read-only mapping только объявленных аргументов. Любое другое исключение — дефект
кода, пробрасывается.

## 5. Выдача Tools агенту

`Agent.tool_refs: tuple[ToolRef, ...] = ()` — пусто: у роли нет Tools (fail closed). Это данные,
как `prompt_ref`: читаются только при сборке `Toolbox` в composition root (ADR-0012); сама роль
ничего не вызывает (ADR-0010).

## 6. Tools v1

| Ref | Параметры | Результат | Поведение |
|---|---|---|---|
| `current_date@v1` | — | `date` (ISO), `weekday` (англ., не зависит от локали), `datetime` (ISO, секунды, со смещением) | часы внедряются (`Callable[[], datetime]`) и читаются при каждом вызове; дата — локальная дата часов; naive-время → `ToolExecutionError` |
| `text_metrics@v1` | `text: string`; `max_chars: integer` (необяз., `≥ 1`) | `characters`, `words`, `sentences`, `paragraphs`; с лимитом — ещё `max_chars`, `within_limit`, `over_by` | `characters` — длина текста как есть; слова — по пробельным символам; предложение заканчивается на `.`/`!`/`?`/`…`, за которыми пробел или конец текста, и считается, если содержит букву/цифру; абзацы разделены пустой строкой; `max_chars < 1` → `ToolExecutionError` |

## 7. Каталог (`tools/catalogue.py`)

`TOOL_DESCRIPTORS` (кортеж) и `TOOLS_BY_REF` (`ref → дескриптор`); ref уникальны. Только
дескрипторы: экземпляры несут внедрённые зависимости и собираются в composition root.

## 8. Ошибки

| Ошибка | База | Когда |
|---|---|---|
| `InvalidToolDescriptorError` | `ToolDomainError` | некорректные id/описание/версия/параметры |
| `ToolGrantError` | `ToolDomainError` | выдачу нельзя исполнить (§4) |
| `ToolExecutionError` | `Exception` (техническая) | Tool не смог дать результат |
| `ValueError` | — | несогласованный `ToolResult` |
| `dataclasses.FrozenInstanceError` | — | попытка изменить дескриптор, параметр, вызов или результат |

`ToolDomainError` наследует `DomainError` (ADR-0017).
