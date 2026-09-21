# GENERATION_SPEC.md — Спецификация генерационного департамента

> Технический контракт по `ADR-0065`, `ADR-0066`, `ADR-0067`. Подчинён `PROJECT.md` §4 п.11/§12,
> `ARCHITECTURE.md` §8/§15 и `DOMAIN_MODEL.md` §8 (PROJECT §17: при конфликте побеждают документы).
> Приёмка — `GENERATION_ACCEPTANCE.md`.
>
> **Статус:** Accepted — **частично.** §1–§4 (порты и вендорные адаптеры, задачи 23.1–23.2)
> действуют. §5 и дальше (QA, борд, гранулярность Run, оркестрация) пишутся в 23.3–23.6 и до тех
> пор **не** предмет реализации. **Дата:** 2026-09-21.

---

## 1. Назначение и граница

Третий департамент: референсное фото + промпты, написанные человеком → **конечный кадр** (Gemini,
«Nano Banana») → **видео от фото к этому кадру** (Higgsfield, Kling 3.0) → QA → Human Approval →
один одобренный видеофайл. Автопубликации нет, агента, пишущего промпты, в v1 нет (`ADR-0065` §5).

Всё добавляется модулями; `Run`, `Task`, `Output`, `Artifact`, `Evaluation`, `HumanReview`,
`ContentDirector` и существующие адаптеры **не меняются** (`PROJECT.md` §4 п.11).

## 2. Порт `ImageGenerator` — `adapters/image_generator.py`

`generate(ImageGenerationRequest) -> GeneratedImage`, синхронно.

- `ImageGenerationRequest(reference, prompt, destination)` — локальный путь к фото, промпт кадра,
  локальный путь для результата. Все три непустые, иначе `ValueError`.
- `GeneratedImage(path, width, height, media_type)` — **собственные замеры** записанного файла
  (`ADR-0056` §1): положительные размеры, непустые `path`/`media_type`.
- `ImageGeneratorError` — техническая ошибка, **не** `DomainError`.
- Вызов — шаг `Workflow` в очередном воркере (`ADR-0049`), **никогда** не Tool (`ADR-0054` §2).
  Повтор после падения тратит деньги ещё раз и перезаписывает тот же файл; ничего не портит.

## 3. Порт `VideoGenerator` — `adapters/video_generator.py`

`submit(VideoGenerationRequest) -> VideoJob`; `collect(VideoJob, destination) -> VideoJobResult`.

- `VideoGenerationRequest(first_frame, last_frame, prompt, duration_s)` — два локальных пути,
  непустой промпт, положительная целая длительность в секундах (не `bool`).
- `VideoJob(job_id)` — непрозрачный для ядра непустой идентификатор.
- `VideoJobState` — `PENDING` / `COMPLETED` / `FAILED` / `REJECTED`. `REJECTED` — отказ модерации
  вендора, **не** техническая ошибка.
- `VideoJobResult(state, video)` — `video` есть **ровно** при `COMPLETED`, иначе `ValueError`.
- `GeneratedVideo(path, duration_ms, width, height, container)` — собственные замеры файла.
- **`submit` неповторяем** (у вендора нет ключа идемпотентности): Workflow фиксирует Task **до**
  вызова и `job_id` — сразу после, как Output успешной Task. Task отправки, найденная `RUNNING`
  при возобновлении, проваливается со стабильной причиной и **не** отправляется повторно
  (`ADR-0066` §3). Раскладка Task/Output — §7, в 23.6.
- **`collect` повторяем**: одна проверка статуса; при `COMPLETED` скачивает файл в `destination`.

Порты берут и отдают **локальные пути**: ни URL вендора, ни имя модели, ни форма запроса не
проходят через контракт (`ADR-0066` §4). Модули `adapters/` импортируют только stdlib и
`domain.*` (`tests/test_adapter_contract.py`).

## 4. Вендорные адаптеры — `infrastructure/` (`ADR-0067`)

Оба — stdlib `urllib`, без новых зависимостей; конструктор принимает `api_url` (для тестов).

### 4.1 `GeminiImageGenerator`

- Один `POST /v1beta/interactions`, заголовок `x-goog-api-key`. Тело: `model`, `input` = текст
  промпта + фото инлайн (`type: image`, `mime_type` по сигнатуре файла, base64), `response_format`
  = `{type: image, aspect_ratio, image_size}`, **`store: false`**.
- **Соотношение сторон:** фото измеряется, запрашивается ближайшее из списка (`1:1`, `2:3`, `3:2`,
  `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`; сравнение по логарифму). Ответ, чьё
  измеренное соотношение дальше 3% от запрошенного, — `ImageGeneratorError`, файл **не** пишется.
- Картинка берётся из `steps[]` типа `model_output` → `content[]` типа `image`. Статус не
  `completed`, нет картинки (текст модели цитируется, до 300 символов), битый base64, не картинка,
  не JSON — `ImageGeneratorError`.
- Фото больше 15 МБ, отсутствующее или не PNG/JPEG/WebP — ошибка **до** любого вызова.
- HTTP-отказ: код + сообщение Google (до 300 символов); ключ не попадает ни в сообщение, ни в `repr`.
- Настройки: `OMEMO_GEMINI_API_KEY`, `OMEMO_GEMINI_IMAGE_MODEL`, `OMEMO_GEMINI_IMAGE_SIZE`
  (`512`/`1K`/`2K`/`4K`) — все обязательны, отсутствующие названы, значений нет.

### 4.2 `HiggsfieldVideoGenerator`

- Авторизация `Authorization: Key <id>:<secret>` — **только** к API Higgsfield. Presigned `PUT` и
  скачивание видео идут **без** неё.
- `submit`: длительность вне 3–15 с — ошибка до любого вызова. Каждый кадр: `POST
  /files/generate-upload-url {content_type}` → `PUT` на `upload_url` с `upload_headers` →
  `public_url`. Затем **один** `POST /<model>` с `prompt`, `image_url`, `last_image_url`,
  `duration`, `sound`. Без повторов. Ответ без `request_id` вида UUID — ошибка.
- `collect`: `job_id` не UUID — ошибка **без** запроса. `GET /requests/{id}/status`:
  `queued`/`in_progress` → `PENDING`; `failed`/`canceled` → `FAILED`; `nsfw` → `REJECTED`;
  `completed` → скачать `video.url` (только `http(s)`, до 300 МБ), измерить, записать атомарно →
  `COMPLETED`. Неизвестный статус, нет видео, файл не читается как MP4 — ошибка, файл не пишется.
- Ошибки HTTP называют документированный смысл кода (401, 403, 404, 422, 423, 503…); ни ключи, ни
  URL в сообщения не попадают.
- Настройки: `OMEMO_HIGGSFIELD_API_KEY_ID`, `OMEMO_HIGGSFIELD_API_KEY_SECRET`,
  `OMEMO_HIGGSFIELD_VIDEO_MODEL` (**только** модели с конечным кадром:
  `kling-video/v3.0/{std,pro,4k}/image-to-video`), `OMEMO_HIGGSFIELD_SOUND` (`on`/`off`).

### 4.3 `media_measure`

PNG / JPEG / WebP — ширина и высота из заголовка; MP4 — длительность из `mvhd` и размер **видео**
дорожки (`hdlr` = `vide`). Всё нечитаемое — `MediaMeasureError`, никаких догадок.

## 5–8. Ещё не специфицировано

§5 критерии QA (23.3), §6 борд (23.4), §7 гранулярность Run и раскладка Task/Output (23.5), §8
оркестрация и точка входа (23.6–23.7).
