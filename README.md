# Concept Content Factory

Фабрика контента на Python: специализированные AI-роли готовят материал, QA проверяет его,
человек принимает решение. Результаты, версии, затраты и состояние прогонов сохраняются в SQLite.

В репозитории реализованы три направления:

| Направление | Что происходит | Точка входа |
|---|---|---|
| Сценарии из брифа | Notion → исследователь → сценарист → QA → ревью | `demo_notion.py`, `factory_service.py` |
| Нарезка видео | Локальный эпизод → расшифровка и сцены → клипы → QA → ревью → публикация при настройке | `demo_clips.py` |
| Генерация истории | Идея → сценарий → голоса → раскадровка → изображения → анимация | Отдельные `demo_story*.py`, `demo_dialogue.py`, `demo_frames.py`, `demo_animate.py` |

Генерация истории пока управляется отдельными командами. HTTP-сервис запускает производство
брифов и обработку решений ревью; нарезка и генерация через него не запускаются.

## Установка

Нужен **Python 3.11 или новее**. Команды ниже выполняются из корня репозитория.

```bash
python3.12 -m venv .venv           # или другой установленный Python 3.11+
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m omemo_content_factory
```

Последняя команда проверяет импорт и конфигурацию, печатает версию; внешних вызовов нет.
На Windows создайте окружение через `py -3.12 -m venv .venv` и активируйте
`.venv\Scripts\Activate.ps1` в PowerShell.

## Конфигурация

Для текстовых ролей запустите мастер и выберите **GPT** или **Claude**:

```bash
python configure_llm.py
# Или сразу нужный сервис:
python configure_llm.py gpt
python configure_llm.py claude
```

Вставьте ключ в терминале: ввод скрыт. Он сохраняется в локальном `.env` с доступом только
владельцу; файл исключён из Git. Настройка не делает платных запросов. Если ключ выбранного
сервиса уже есть в `.env` или окружении, он используется повторно.

| Выбор | Прямой API | Стартовая модель | Ключ |
|---|---|---|---|
| GPT | OpenAI | `gpt-4.1-mini` | `OPENAI_API_KEY` |
| Claude | Anthropic | `claude-haiku-4-5-20251001` | `ANTHROPIC_API_KEY` |

Мастер подставляет модель, стандартные цены и лимит ответа 16000 токенов. Это стартовые
профили; качество выбора сцен на конкретных сериях нужно проверять. Цены проверены 13.10.2026:
[GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) — $0.40/$1.60,
[Claude Haiku 4.5](https://platform.claude.com/docs/en/models/haiku-4-5/overview) — $1/$5
за миллион входных/выходных токенов. Учёт расходов использует эти ставки и может отличаться
от счёта провайдера из-за кеширования и других условий тарифа.

Для другой модели задайте её ID и обе цены; параметры `--max-tokens` и `--thinking` можно менять:

```bash
python configure_llm.py gpt --model MODEL_ID --input-price INPUT_RATE --output-price OUTPUT_RATE
python check_llm.py   # проверка подключения: один платный запрос к выбранной модели
```

Скрипты `demo*.py`, `factory_service.py` и `check_llm.py` автоматически читают `.env` рядом
с собой. Переменные, уже экспортированные в терминале, имеют приоритет — после переключения
модели уберите старые экспорты или откройте новый терминал. Файл не нужно выполнять через `source`.
Общий профиль `OMEMO_*__DEFAULT` действует на все текстовые роли, включая выбор моментов,
тексты постов и QA. Существующие настройки отдельной роли имеют приоритет; мастер сообщает
о них. Параметры выбранного направления добавляйте из [.env.example](.env.example), сохраняя
созданный мастером файл. Подключения Notion, генерации медиа и публикации настраиваются отдельно.

## Бриф → сценарий → ревью

```bash
python demo_factory.py                    # встроенный пример брифа
python demo_notion.py <notion-page-id>     # бриф из настроенной базы Notion
python factory_service.py                # HTTP-сервис для n8n
```

Запускайте нужный вариант. Путь через Notion требует `OMEMO_NOTION_*`, сервис — ещё
`OMEMO_SERVICE_TOKEN`. Ревью доступно в Notion или Google Docs; без настроенного сервиса ревью
решения можно вводить флагами CLI:

```bash
python demo_factory.py --approve
python demo_factory.py --request-changes "Добавить источник для утверждения"
python demo_factory.py --reject "Не соответствует брифу"
```

Одобрить можно только материал, прошедший QA. Доработка запускает заново автора текущего
материала, создаёт новую версию и снова проходит проверки. Повторный запуск продолжает
сохранённый прогон. По умолчанию база — `.omemo/runs.sqlite3`; для отдельного нового запуска
укажите новый путь в `OMEMO_RUN_STORE_PATH`, сохранив предыдущую базу.

Настройка баз и ревью: [инструкция для брифов](docs/operations/briefs.md).
Автоматизация: [n8n/README.md](n8n/README.md).

## Нарезка видео

**Локальное видео без Notion, до публикации в YouTube Shorts:**

```sh
python factory.py '/полный/путь/серия.mp4' --profile my-channel
```

При первом запуске команда запрашивает недостающие настройки GPT/Claude, Whisper и
Google OAuth. Затем готовит клипы и TXT и требует отдельное `publish` перед каждой
загрузкой. Системные инструменты устанавливаются заранее.
[Установка и первый запуск](LOCAL_FACTORY.md). `--draft-only` готовит без публикации.

Рядом с клипом создаётся TXT для **YouTube Shorts**: `clip.unique.mp4` → `clip.unique.txt`,
с названием и описанием. Этот шаг встроен в конвейер: нарезка → обработка → QA →
название и описание для YouTube Shorts → TXT рядом с видео → ревью.
Для генерации текста нужен настроенный GPT/Claude. Если текста ещё нет, TXT не создаётся.
Существующие TXT не перезаписываются, поэтому их можно редактировать вручную. Они предназначены
для ручной загрузки; автоматическая публикация читает проверенный текст из базы, не из TXT.


После нарезки и встраивания субтитров каждый новый клип проходит обработку **перед QA**:
скорость 0,98× с сохранением высоты голоса, масштаб 94%, постоянная частота кадров,
лёгкие воспроизводимые изменения цвета, резкости и наклона, пересборка метаданных.
Исходный файл сохраняется; результат — `*.unique.mp4`, рядом `*.unique.processing.json`.
Повторный запуск использует проверенный результат и не замедляет видео второй раз.
Это изменение файла, а не гарантия оригинальности для площадки.

Настройки: `OMEMO_VIDEO_SPEED`, `OMEMO_VIDEO_SCALE`, `OMEMO_VIDEO_CRF`,
`OMEMO_VIDEO_PRESET`, `OMEMO_VIDEO_LOOK` (`clean`, `subtle`, `strong`).
Зеркалирование `OMEMO_VIDEO_MIRROR=false` по умолчанию: оно переворачивает и вшитые субтитры.
Обработка применяется только к нарезкам. Ошибка обработки не пропускает исходный клип в QA.
Старые одобренные клипы без этого этапа не заменяются автоматически: для новой публикации
потребуется новый прогон с новой идентичностью эпизода/Run либо отдельная проверенная миграция.
Для реального запуска нужны `ffmpeg` и `ffprobe`; HDR-вход модуль отклоняет.


Доступен режим **`moments`**: в карточке эпизода выберите `Mode= moments` и настройте GPT/Claude через
`configure_llm.py` (либо отдельную роль `CLIP_MOMENT_PICKER_V1` в `.env.example`). Модель выбирает по расшифровке
законченные моменты; QA и ручное ревью остаются обязательными. По умолчанию — до 12 моментов
длиной 60–120 секунд. Пределы: `OMEMO_CLIP_MOMENT_MIN_MS`, `OMEMO_CLIP_MAX_MS`,
`OMEMO_CLIP_MOMENT_COUNT`. Качество реального отбора ещё предстоит проверить на исходнике.

Новый вид субтитров включается вместе с увеличенным кадром на размытом фоне:

```bash
export OMEMO_CLIP_FILL=blur
export OMEMO_CLIP_PICTURE_HEIGHT=0.79
export OMEMO_CLIP_CAPTION_COLOR=yellow
```

Слова выводятся по одному в центре, не дольше 0,9 секунды каждое. Их время приблизительно
распределяется внутри фразы Whisper, это не точное выравнивание каждого слова по аудио.
Увеличение обрезает края кадра. Старое оформление остаётся при незаданных параметрах.
Для фильтрации музыки при распознавании можно указать `OMEMO_WHISPER_VAD_MODEL` — путь
к совместимой модели Silero для whisper.cpp. Она отдельно не скачивается автоматически.


Нужны локальные `ffmpeg`/`ffprobe`, ffmpeg с `libass` для субтитров, `whisper-cli` и файл модели
Whisper. `fpcalc` используется для поиска повторяющихся заставок. В `.env.example` есть настройки
борда эпизодов, папки с видео и ревью.

```bash
python demo_clips.py <notion-page-id-of-episode>
```

Полная последовательность — в [CLIPPING_RUNBOOK.md](CLIPPING_RUNBOOK.md).
Повторный запуск читает решения и продолжает производство. Публикация (напрямую в YouTube, Instagram, TikTok) требует отдельного запуска
`python demo_clips.py --publish <id>` и отправляет только одобренные клипы с текстом поста.
Обычный запуск ничего не публикует даже при настроенном ключе. Текущий режим: пользователь
пишет «постинг», получает предпросмотр видео, канала, названия и описания и явно одобряет
публикацию. Целевой канал — `@tsxmoon-n7s`; прямое подключение OAuth подтверждено 2026-10-13. Расписания нет.

## Генерация истории

Сначала настройте LLM-роли, один сервис озвучки и BytePlus для Seedream/Seedance.
Для работы со звуком и сборки видео нужен ffmpeg. Команды выполняются поэтапно:

```bash
python demo_story.py "Идея истории"
```

Скрипт напечатает путь `generation-tests/stories/<story>.txt`. Перед озвучкой замените
`voice:<key>` в его репликах на голоса выбранного сервиса. Для этого же имени `<story>`:

```bash
python demo_dialogue.py generation-tests/stories/<story>.txt generation-tests/dialogue/<story>/dialogue.wav
python demo_storyboard.py generation-tests/stories/<story>.txt
python demo_frames.py sheets generation-tests/stories/<story>.txt
python demo_frames.py shots generation-tests/stories/<story>.txt
python demo_animate.py plan generation-tests/stories/<story>.txt
python demo_animate.py run generation-tests/stories/<story>.txt
python demo_animate.py join generation-tests/stories/<story>.txt
```

`plan` рассчитывает тайминг и оценку цены без отправки генераций. `run` запускает анимацию,
а `join` собирает **видео без звука** в `generation-tests/frames/<story>/silent.mp4`.
Озвучка остаётся отдельным WAV; финальное наложение звука пока выполняется отдельно.
Точечная перерисовка: `demo_frames.py sheets|shots <story.txt> [keys|numbers] --note "Правка"`.

## Где искать код и документацию

| Путь | Назначение |
|---|---|
| `src/omemo_content_factory/domain/` | Прогоны, задачи, результаты, версии, QA и решения человека |
| `src/omemo_content_factory/application/` | Последовательность производства и возобновление прогонов |
| `src/omemo_content_factory/agents/`, `prompts/` | Роли, схемы ответов и версионированные промпты |
| `src/omemo_content_factory/adapters/`, `infrastructure/` | Контракты и реализации внешних сервисов |
| `src/omemo_content_factory/composition.py` | Сборка компонентов из настроек |
| `tests/` | Тесты и проверки контрактов |

Архитектурная иерархия: [PROJECT.md](PROJECT.md) → [ARCHITECTURE.md](ARCHITECTURE.md) →
[ROADMAP.md](ROADMAP.md) → [ADR](docs/adr/README.md) → код.
Модель данных — [DOMAIN_MODEL.md](DOMAIN_MODEL.md), спецификации и критерии приёмки —
`*_SPEC.md` / `*_ACCEPTANCE.md`. История сессий и старый нумерованный список задач —
[архив заметок](docs/history/development-notes.md).

`demo.py` — ранний пример Research → Writer → Editor с отдельными `OMEMO_LLM_*` настройками;
для основного пути используйте `demo_factory.py`.

## Проверки

В активированном окружении:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest
```

Порядок разработки и настройка pre-commit: [CONTRIBUTING.md](CONTRIBUTING.md).
Лицензия: [MIT](LICENSE).

### YouTube Shorts напрямую, с одобрением в чате

Для @tsxmoon-n7s выбран прямой Google OAuth. Подключение: `configure_youtube.py`.
Предпросмотр готового MP4 + соседнего TXT: `post_youtube.py`; отправка только после
явного апрува пользователя. OAuth для этого канала подтверждён 2026-10-13. Реальная загрузка ещё не проверялась.
Полная инструкция: [YOUTUBE_SETUP.md](YOUTUBE_SETUP.md).

### Instagram Reels и TikTok напрямую

Instagram — через Facebook-страницу (`configure_instagram.py`), TikTok — черновиками до
аудита приложения, потом напрямую (`configure_tiktok.py`). Предпросмотр и отправка по
отпечатку: `post_clip.py --to instagram|tiktok`; в локальной фабрике — `--also instagram,tiktok`.
Инструкция: [SOCIAL_SETUP.md](SOCIAL_SETUP.md).

## Дополнительная настройка

[Проверка окружения и другие провайдеры](docs/SETUP.md): `python doctor.py`,
`python configure_llm.py --list`. Для быстрого старта с локальным видео — [LOCAL_FACTORY.md](LOCAL_FACTORY.md).
