# Setting up a machine (no AI assistant needed)

This is the one page for running the factory on a new device. Decisions behind it: ADR-0093.

## 1. Install

```bash
git clone <this repo> concept && cd concept
scripts/setup.sh            # --no-dev on a machine that only runs the factory
```

It finds Python ≥ 3.11, makes `.venv`, installs the package, creates `.env` from `.env.example`
(never overwrites yours) and prints a readiness report. Re-running is safe.

Windows: use **WSL** (Ubuntu); everything below is the Linux path.

## 2. Ask the machine what it lacks

```bash
.venv/bin/python doctor.py                 # all departments
.venv/bin/python doctor.py clipping posting
```

`[MISSING]` lines are required for that department and come with an install hint; `[optional]`
lines are advice. The report shows whether a variable is **set**, never its value, so it is safe to
paste. Exit code 1 only when a required item of a requested department is missing.

Every entrypoint (`demo_*.py`, `factory_service.py`, `doctor.py`) reads `.env` itself; a variable
already exported in your shell wins over the file. The core library reads the process environment
only.

## 3. Choosing a model (any provider)

The factory's text roles (QA, post writer, story writer, storyboard) call a language model. **Any**
provider works (ADR-0094): Anthropic, or anything that speaks the OpenAI dialect — xAI (Grok),
OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Together, a local Ollama or LM Studio, or your own
gateway. One command writes the choice for **every** role into `.env`:

```bash
python configure_llm.py --list                                   # the presets and their key variables
python configure_llm.py xai --model grok-4 --input-price 3 --output-price 15
python configure_llm.py openrouter --model <vendor/model> --input-price 1 --output-price 4
python configure_llm.py ollama --model llama3.1 --free           # local, no key, no cost
python configure_llm.py openai-compatible --model m --base-url https://llm.example/v1 \
    --key-env MY_GATEWAY_KEY --input-price 1 --output-price 2
python configure_llm.py anthropic --model <claude model> --thinking adaptive \
    --input-price 3 --output-price 15
```

- **Prices are yours to give** — per one million tokens, from your provider's price page. The
  factory never guesses a rate; every call's cost is computed from the numbers you wrote.
- **The key is yours to add**, never written by the command: put it in `.env` (or export it) under
  the variable the command prints (`XAI_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, …).
- Then **`python check_llm.py`** — one tiny real call that proves the key, the model name and the
  price, and prints the exact cost. Run it once per new provider.
- The block lives between `# >>> factory model` markers in `.env`; re-running replaces it, nothing
  else in the file is touched. One role can differ: add `OMEMO_MODEL__QA_AGENT_V1=...` (any
  `OMEMO_<NAME>__<ROLE>` beats the `__DEFAULT`).

If a provider rejects a request, the error names its own answer. Three settings fix most quirks
without code (add them to the block, e.g. `OMEMO_WIRE__DEFAULT=responses`):

| Setting | Values | When |
|---|---|---|
| `OMEMO_WIRE__*` | `chat` (default), `responses` | xAI marks `chat` deprecated; switch if it is ever removed |
| `OMEMO_STRUCTURED__*` | `tool` (default), `tool-required`, `json` | a server that rejects a named tool choice → `tool-required`; a local model with weak function calling → `json` |
| `OMEMO_THINKING__*` | `inherit` (default), `effort:low|medium|high` | a reasoning model that takes `reasoning_effort` |

### Path for someone with only Cursor and a Grok key

1. Download the repo and open the folder in Cursor (it reads `.cursor/rules/` and `AGENTS.md` itself).
2. In Cursor's terminal: `scripts/setup.sh`.
3. `python configure_llm.py xai --model <grok model> --input-price <n> --output-price <n>` (prices from xAI's pricing page).
4. Put `XAI_API_KEY=...` into `.env`.
5. `python check_llm.py` → expect `answer 'ok'` and a cost line.
6. `python doctor.py` → fix what it lists for the departments you use; then run the entrypoints
   (`demo_cut.py`, `demo_post_queue.py`, …). No Anthropic key is asked for anywhere.

## 4. What each department needs

| Department | What it does | Needs |
|---|---|---|
| `core` | text roles: QA, post writer, story writer | Python ≥ 3.11, a model chosen with `configure_llm.py` and **its** key |
| `clipping` | episode file → clips with captions (`demo_cut.py`, `demo_clips.py`) | `ffmpeg` **with libass**, `ffprobe`, `whisper-cli` + a GGML model, optional `fpcalc` |
| `posting` | clips → YouTube etc. through upload-post (`demo_post_queue.py`) | `OMEMO_UPLOAD_POST_API_KEY`, `_PROFILE`, `_YOUTUBE_PRIVACY` |
| `generation` | voiced story videos | `OMEMO_GEMINI_API_KEY`; optionally Higgsfield / Seedream / Seedance / ElevenLabs keys |

### ffmpeg with libass (captions)

| OS | Command |
|---|---|
| macOS | `brew uninstall ffmpeg; brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg` (the stock formula has no libass) |
| Debian / Ubuntu | `sudo apt install ffmpeg` |

Check: `ffmpeg -hide_banner -filters | grep subtitles`.

### whisper.cpp and a model

| OS | Command |
|---|---|
| macOS | `brew install whisper-cpp` |
| Linux | build from github.com/ggml-org/whisper.cpp and put `whisper-cli` on `PATH` |

Download a GGML model (for Russian speech `ggml-large-v3-turbo.bin` works well) from the whisper.cpp
model repository and set `OMEMO_WHISPER_MODEL=/path/to/model.bin` in `.env`.
Set `OMEMO_WHISPER_LANGUAGE=ru` (or the audio's language; the default is auto-detect, which
sometimes invents text over music).

### fpcalc (optional — finds opening titles and end credits by sound)

`brew install chromaprint` · `sudo apt install libchromaprint-tools`

## 5. Typical commands

```bash
.venv/bin/python demo_cut.py episode.mp4 clips/out --minutes 2     # a file in OMEMO_EPISODE_ROOT
.venv/bin/python demo_post_queue.py post-queue                     # one pass of the post queue
```

Optional clip settings (all in `.env.example`): `OMEMO_CLIP_CAPTION_COLOR` (white | yellow |
#RRGGBB), `OMEMO_CLIP_MUSIC` + `OMEMO_CLIP_MUSIC_VOLUME` (a quiet track under every clip),
`OMEMO_CLIP_CANVAS`, `OMEMO_UPLOAD_POST_YOUTUBE_PRIVACY` (`unlisted` videos are not shown in feeds
or search — use `public` to be discoverable).

Run the post queue on a schedule with `scripts/com.concept.post-queue.plist` (macOS launchd) or
cron: `0 */2 * * * cd /path/to/concept && scripts/run_post_queue.sh post-queue`.

## 6. The checks

```bash
ruff check . && ruff format --check . && mypy && pytest
```

## About "Claude"

Nothing here needs Claude. The core is provider-neutral (ports for the model, storage, boards,
publishers); the text roles run on any provider you choose in section 3. `CLAUDE.md` is a running
log for Claude Code sessions — useful history, not required reading. Two things are still
Anthropic-only: the `anthropic` package is installed with the project (a Grok-only machine simply
never uses it), and image input for the generation roles' vision steps is not built for any
provider yet.
