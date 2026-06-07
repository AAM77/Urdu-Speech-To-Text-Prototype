# Urdu Audio Article Prototype

A local Python prototype that turns an Urdu religious-lecture audio file into a
polished, standalone American English article in five modular stages:

```
Audio (.mp3)
  │
  ▼
1. Chunker         (5-minute chunks, 60-second overlap)
2. Transcriber     (Urdu script per chunk)
3. Reconciler      (deterministic overlap dedupe -> one Urdu transcript)
4. Translator      (American English, religious terminology preserved)
5. Article writer  (8th-grade-level standalone article)
```

Each stage saves both a JSON artifact and a Markdown file, plus a manifest with
metadata (model, prompt version, hashes, cost estimate, cache status, etc.).
You can stop after any stage, download the artifact, and later upload it back
into the next stage without re-running earlier paid calls.

The project ships with a Streamlit UI, a Typer CLI, a root `Makefile` for
common stage workflows, and a deterministic **fake-provider mode** so you can
exercise everything end-to-end without an API key or network access. Real
OpenAI calls are gated behind explicit configuration and an interactive cost
confirmation.

---

## What this prototype does

- Accepts Urdu audio that may include Urdu, Arabic phrases, and English
  code-switching.
- Splits audio into 5-minute chunks with a 60-second overlap (configurable).
- Transcribes each chunk into Urdu script.
- Reconciles overlapping chunk transcripts into one clean Urdu transcript.
- Translates the reconciled transcript into American English, preserving
  religious terminology with Hans-Wehr-style transliteration in italics.
- Generates a polished standalone American English article.
- Writes downloadable JSON + Markdown artifacts at every stage.
- Lets you upload any prior-stage artifact into the next stage, both via UI
  and CLI.
- Estimates cost before any paid call, requires explicit confirmation, and
  hard-blocks any projected total above the configured cap.

## What this prototype does **not** do (yet)

- Multiple chunk sizes (only one 5-minute set per run).
- Multi-pass transcription / cross-provider review.
- Diarization or speaker labels.
- Word-level timestamps.
- Hosted deployment, authentication, or a database.
- Theological fact-checking.

---

## Tech stack and version policy

| Component | Used here | Notes |
|---|---|---|
| Python | `>= 3.12` | Tested on 3.12.0; works on 3.13/3.14 |
| OpenAI SDK | `>= 2.30, < 3` | Latest tested: 2.32.0 |
| Pydantic | `>= 2.13, < 3` | |
| pydantic-settings | `>= 2.13, < 3` | |
| Streamlit | `>= 1.55, < 2` | Latest tested: 1.56.0 |
| Typer | `>= 0.24, < 1` | |
| Rich | `>= 14.3, < 16` | |
| tiktoken | `>= 0.10, < 1` | |
| rapidfuzz | `>= 3.10, < 4` | Used for deterministic overlap dedupe |
| pytest | `>= 9.0, < 10` | Dev only |
| FFmpeg / ffprobe | system | Tested with 7.x and 8.x |

Dependencies are intentionally lower-bounded ("compatible release"), not
pinned, so `pip install` picks up patches automatically. To upgrade, edit
`pyproject.toml` (and `requirements.txt`).

---

## 1. Install required tools

You need three things before installing the Python dependencies:

### 1a. Python 3.12+

Verify with:

```bash
python3 --version
```

If you do not have Python 3.12+, install it:

- **macOS (Homebrew)**: `brew install python@3.13`
- **macOS / Linux (asdf)**: `asdf install python 3.13.13 && asdf global python 3.13.13`
- **Linux (apt)**: `sudo apt update && sudo apt install -y python3.13 python3.13-venv`
- **Windows**: download from <https://www.python.org/downloads/windows/>

### 1b. FFmpeg + ffprobe

The chunker calls `ffprobe` to read audio duration and `ffmpeg` to slice the
audio. Both tools must be on your `PATH`.

Verify with:

```bash
ffmpeg -version
ffprobe -version
```

Install if missing:

- **macOS (Homebrew)**: `brew install ffmpeg`
- **Linux (apt)**: `sudo apt update && sudo apt install -y ffmpeg`
- **Linux (dnf)**: `sudo dnf install ffmpeg`
- **Windows (winget)**: `winget install Gyan.FFmpeg`
  (or download from <https://ffmpeg.org/download.html>)

### 1c. Git (only if you are cloning from a Git remote)

```bash
git --version
```

---

## 2. Set up the project

From the repository root:

```bash
# 2.1 Create a virtual environment
python3 -m venv .venv

# 2.2 Activate it
#   macOS / Linux:
source .venv/bin/activate
#   Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# 2.3 Upgrade pip (recommended)
pip install --upgrade pip

# 2.4 Install the project + dev dependencies
pip install -e ".[dev]"
```

If you prefer not to install the project as editable, you can use
`pip install -r requirements.txt` instead, but you will lose the
`urdu-pipeline` console-script entry point.

---

## 3. Configure environment variables

Copy the example env file and edit it:

```bash
cp .env.example .env
```

The default `.env` runs the prototype in **fake-provider mode** — no API key
required. Switch to real-provider mode only after you have followed
[Section 6](#6-using-real-openai-providers).

Important variables:

| Variable | Default | Purpose |
|---|---|---|
| `PIPELINE_PROVIDER_MODE` | `fake` | `fake` or `real`. |
| `OPENAI_API_KEY` | _empty_ | Required only when `real`. |
| `OPENAI_ORG_ID` | _empty_ | Optional. Only set if your account has multiple orgs. |
| `OPENAI_PROJECT_ID` | _empty_ | Optional. Only set if you use project-scoped keys. |
| `DEFAULT_BUDGET_USD` | `30` | Per-run budget target (warning above this). |
| `HARD_CAP_USD` | `60` | Pipeline refuses to spend more than this. |
| `COST_SAFETY_MARGIN` | `0.20` | 20% safety buffer on every estimate. |
| `DEFAULT_CHUNK_LENGTH_SECONDS` | `300` | 5 minutes. |
| `DEFAULT_OVERLAP_SECONDS` | `60` | 1-minute overlap between chunks. |
| `MAX_CHUNK_MB` | `24` | Warn when a chunk exceeds 24 MB (OpenAI limit is 25 MB). |
| `ACCEPTED_AUDIO_EXTENSIONS` | `mp3,wav,m4a,flac,ogg,webm,mp4` | **Configurable**. Comma-separated, case-insensitive. Default expects `.mp3`. |
| `TRANSCRIPTION_MODEL` | `gpt-4o-transcribe` | Configurable. |
| `TRANSLATION_MODEL` | `gpt-5.5` | Configurable. |
| `ARTICLE_MODEL` | `gpt-5.5` | Configurable. |
| `RECONCILIATION_MODEL` | `gpt-5.5` | Configurable (the prototype default reconciler is deterministic; this is reserved for future use). |
| `OUTPUT_ROOT` | `runs` | Where stage artifacts are saved. |
| `CACHE_ROOT` | `.cache_pipeline` | Where stage outputs are cached so reruns are free. |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |
| `PROMPT_VERSION` | `v1` | Bump if you edit prompts. Used for cache keys. |

### Audio file types are configurable

You will be feeding `.mp3` files initially, which works with the default
configuration. To add or change accepted extensions, edit
`ACCEPTED_AUDIO_EXTENSIONS` in `.env`. Extensions are matched
case-insensitively, with or without the leading dot.

The chunker, the CLI's `estimate` command, and the Streamlit upload widgets
all use this list, so changing it in one place is enough.

---

## 4. Run tests

The full test suite uses fake providers and never touches the network.
The examples below assume your virtual environment is already activated.
If not, replace `python` with `.venv/bin/python`.

```bash
# All tests
python -m pytest

# Just the unit tests
python -m pytest tests/unit

# Just the safe integration tests
python -m pytest tests/integration_safe

# The Makefile orchestration tests only
python -m pytest tests/integration_safe/test_makefile.py -v

# A single test file with verbose output
python -m pytest tests/unit/test_chunker.py -v
```

Expected: 72 tests pass on the current codebase.

### What the tests cover

- Unit tests for config, schemas, artifacts, cache, costs, chunking, stage
  logic, and standalone helpers.
- Safe integration coverage for the fake-provider pipeline end to end.
- Real subprocess coverage for the `Makefile` wrapper in
  `tests/integration_safe/test_makefile.py`.

The `Makefile` tests are intentionally not "mock-only" wrapper tests. They:

- invoke `make` as a subprocess
- run the real CLI entry points for each stage
- verify prerequisite failures and argument validation
- verify latest-run auto-resolution
- verify cumulative `to-*` targets stop at the expected stage
- verify `CONFIRM_PAID_RUN`, `CHUNK_LENGTH_SECONDS`, and
  `OVERLAP_SECONDS` are forwarded correctly

To keep those tests deterministic and local, they fake only the external
`ffprobe` / `ffmpeg` executables with temporary shell shims. The Python
orchestration and artifact flow are real.

---

## 5. Run the pipeline in fake-provider mode

Fake mode requires no API key, never makes network calls, and returns
deterministic output. Use it to check your wiring or to demo the UI.

### 5a. Makefile wrapper

The root `Makefile` is the quickest way to run an individual stage or a
cumulative pipeline sequence without remembering every artifact path.

Show all available targets:

```bash
make help
```

Print the latest run directory under `OUTPUT_ROOT`:

```bash
make latest-run
```

Single-stage targets:

```bash
# 1. Run only the chunking stage
make chunk AUDIO="inputs/your_lecture.mp3"

# 2. Run only transcription on an existing run
make transcribe RUN_DIR="runs/<run-dir>"

# 3. Run only reconciliation on an existing run
make reconcile RUN_DIR="runs/<run-dir>"

# 4. Run only translation on an existing run
make translate RUN_DIR="runs/<run-dir>"

# 5. Run only article generation on an existing run
make article RUN_DIR="runs/<run-dir>"
```

If `RUN_DIR` is omitted for `transcribe`, `reconcile`, `translate`, or
`article`, the Makefile automatically resolves the newest run under
`OUTPUT_ROOT` and uses that.

Cumulative targets:

```bash
# 6. Chunk + transcribe
make to-transcribe AUDIO="inputs/your_lecture.mp3"

# 7. Chunk + transcribe + reconcile
make to-reconcile AUDIO="inputs/your_lecture.mp3"

# 8. Chunk + transcribe + reconcile + translate
make to-translate AUDIO="inputs/your_lecture.mp3"

# 9. Chunk + transcribe + reconcile + translate + article
make to-article AUDIO="inputs/your_lecture.mp3"
```

Useful overrides:

```bash
# Real-provider mode requires explicit confirmation for paid stages
make to-article AUDIO="inputs/your_lecture.mp3" CONFIRM_PAID_RUN=1

# Override chunk sizing for chunk / to-* targets
make chunk AUDIO="inputs/your_lecture.mp3" CHUNK_LENGTH_SECONDS=240 OVERLAP_SECONDS=45

# Write runs somewhere else for a single command
make to-translate AUDIO="inputs/your_lecture.mp3" OUTPUT_ROOT="tmp/demo-runs"

# Use a different interpreter if needed
make chunk AUDIO="inputs/your_lecture.mp3" PYTHON=".venv/bin/python"
```

Notes:

- `AUDIO=...` is required for `chunk` and all `to-*` targets.
- `RUN_DIR=...` is optional for single-stage targets after chunking.
- `CONFIRM_PAID_RUN=1` is only needed for the paid stages in real-provider
  mode.
- `OUTPUT_ROOT` is exported into the CLI process, so the Makefile and the app
  agree on where runs are created and resolved.

### 5b. Local API-backed workflow

The backend conversion also includes a local Docker Compose parity stack for
API, processor, PostgreSQL, MinIO, Redis, and an optional Nginx proxy. See
`docs/local_api_workflow.md` for setup commands and the API route workflow.
See `docs/operator_guide.md` for user administration, token revocation,
cleanup, backup, restore, smoke tests, and cost monitoring.

### 5c. Streamlit UI

```bash
streamlit run src/urdu_pipeline/ui/streamlit_app.py
```

Open the URL Streamlit prints (usually <http://localhost:8501>). The
**Full Pipeline** tab uploads an audio file and runs every stage, downloading
each artifact once it is ready. Per-stage tabs let you jump in or resume from
any prior-stage artifact.

### 5d. CLI (full pipeline)

```bash
python -m urdu_pipeline.cli run-all \
  --audio path/to/your_lecture.mp3 \
  --budget 30 \
  --provider-mode fake
```

(or use the installed entry point: `urdu-pipeline run-all ...`).

A new directory is created under `runs/<date>_<slug>_<id>/` containing every
stage's JSON + Markdown output and a `exports/full_run_export.zip`.

### 5e. CLI (per stage)

```bash
# Estimate only — no calls, no writes
python -m urdu_pipeline.cli estimate --audio path/to/your_lecture.mp3

# 1. Chunk an audio file
python -m urdu_pipeline.cli chunk \
  --audio path/to/your_lecture.mp3

# 2. Transcribe an existing chunk manifest
python -m urdu_pipeline.cli transcribe \
  --chunk-manifest runs/<run-id>/artifacts/chunk_manifest.json

# 3. Reconcile a raw transcript
python -m urdu_pipeline.cli reconcile \
  --transcript runs/<run-id>/artifacts/raw_urdu_transcript.json

# 4. Translate the reconciled transcript
python -m urdu_pipeline.cli translate \
  --transcript runs/<run-id>/artifacts/reconciled_urdu_transcript.json

# 5. Generate the final article
python -m urdu_pipeline.cli article \
  --translation runs/<run-id>/artifacts/english_translation.json

# Validate any artifact JSON against its schema
python -m urdu_pipeline.cli validate-artifact \
  --artifact runs/<run-id>/artifacts/final_article.json

# Re-export a run as a ZIP (artifacts only by default)
python -m urdu_pipeline.cli export-run \
  --run-dir runs/<run-id>
# Add chunks to the export:
python -m urdu_pipeline.cli export-run \
  --run-dir runs/<run-id> \
  --include-chunks
```

---

## 6. Using real OpenAI providers

### 6a. Get an OpenAI API key

1. Go to <https://platform.openai.com/signup> and create an account
   (separate from a ChatGPT subscription).
2. Verify your email address and phone number when prompted.
3. Open <https://platform.openai.com/account/billing> and add a payment
   method, plus a starting credit balance (`$5` is enough to test).
4. Go to <https://platform.openai.com/api-keys> and click **Create new
   secret key**. Give it a descriptive name (e.g. `urdu-prototype-local`).
5. **Copy the key immediately.** You will not be able to view it again.
   The key looks like `sk-proj-...` (project-scoped) or `sk-...` (legacy).
6. Optionally set monthly usage limits at
   <https://platform.openai.com/account/limits> as a second safety net.

### 6b. Save the key in `.env`

Open `.env` and set:

```env
PIPELINE_PROVIDER_MODE=real
OPENAI_API_KEY=sk-proj-your-real-key-here
```

If you belong to multiple organizations / projects, you can also set
`OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`. Otherwise leave them blank.

The `.env` file is git-ignored. The application never logs the key, and
the Settings UI shows only whether a key is set (not its value).

### 6c. Verify model availability and pricing

OpenAI changes its model lineup and pricing fairly often. Before running a
paid pipeline:

1. Check current model IDs at <https://platform.openai.com/docs/models>.
   Update `TRANSCRIPTION_MODEL`, `TRANSLATION_MODEL`, `ARTICLE_MODEL`, and
   `RECONCILIATION_MODEL` in `.env` if needed.
2. Check current pricing at <https://openai.com/api/pricing/>. If you switch
   to a model that is not in
   `src/urdu_pipeline/config/pricing.py`, the budget guard will refuse to
   issue real calls until you add the price.

### 6d. Estimate, then run

```bash
# 1. Estimate first — no API calls
python -m urdu_pipeline.cli estimate --audio path/to/your_lecture.mp3

# 2. Run with explicit confirmation
python -m urdu_pipeline.cli run-all \
  --audio path/to/your_lecture.mp3 \
  --budget 30 \
  --provider-mode real \
  --confirm-paid-run
```

Without `--confirm-paid-run`, the CLI prints the estimate and exits with
status `2`. The Streamlit UI shows the same estimate and requires you to tick
a confirmation checkbox.

The pipeline will hard-stop if `(accumulated cost + next-stage estimate) ×
(1 + safety margin)` would exceed `HARD_CAP_USD`.

---

## 7. Where outputs are saved

For each run, a directory is created under `OUTPUT_ROOT` (default `runs/`)
following the layout below:

```
runs/
  2026-04-27_yourfile_a1b2c3d4/
    input/
      yourfile.mp3                        ← copy of the input audio
    chunks/
      chunk_0001.mp3 … chunk_NNNN.mp3     ← 5-minute chunks
    artifacts/
      chunk_manifest.json
      chunk_summary.md
      raw_urdu_transcript.json
      raw_urdu_transcript.md
      reconciled_urdu_transcript.json
      reconciled_urdu_transcript.md
      english_translation.json
      english_translation.md
      final_article.json
      final_article.md
    exports/
      full_run_export.zip                 ← artifacts/ in a single ZIP
```

The cache (default `.cache_pipeline/`) is **separate** from any single run, so
re-running the same audio with the same models / prompts is free.

---

## 8. Resuming from a downloaded artifact

You can stop at any stage, download the JSON, and later upload it back into a
later stage — no need to re-run earlier (paid) stages.

### Streamlit

Each per-stage tab (Transcription, Reconciliation, Translation, Article) has
an "Or upload …" file picker that accepts the previous stage's JSON
artifact. The validator rejects wrong-stage artifacts with a clear error.

### CLI

Pass the artifact path with `--chunk-manifest`, `--transcript`, or
`--translation` as appropriate (see Section 5d). All commands accept the
absolute path to any prior-run artifact JSON.

---

## 9. How budget controls work

For each paid stage:

1. The estimator computes a per-stage cost from token / minute counts and the
   pricing table in `src/urdu_pipeline/config/pricing.py`.
2. The budget guard adds it to the run's accumulated cost and multiplies by
   `1 + COST_SAFETY_MARGIN`.
3. If the projected total exceeds the hard cap (`HARD_CAP_USD`, default
   `$60`), the call is refused and the pipeline raises
   `BudgetViolationError`.
4. If it exceeds the per-run budget (`DEFAULT_BUDGET_USD` or the value
   passed via `--budget`), the call is allowed but a warning is logged.
5. Successful calls update `accumulated_cost_usd`. Cached calls do not.

If a model has no pricing entry, `MissingPricingError` is raised before any
real call is made. Fake-provider mode logs a warning instead and continues.

---

## 10. Changing models, prompts, chunk size, etc.

| To change… | Edit… |
|---|---|
| Default models | `.env` (`TRANSCRIPTION_MODEL`, `TRANSLATION_MODEL`, `ARTICLE_MODEL`, `RECONCILIATION_MODEL`) |
| Pricing table | `src/urdu_pipeline/config/pricing.py` |
| Prompt text | `src/urdu_pipeline/prompts/*_v1.md`. Bump `PROMPT_VERSION` and create `*_v2.md` files when changing wording in a way that should invalidate the cache. |
| Glossary | `src/urdu_pipeline/prompts/glossary.md` |
| Chunk length / overlap | `.env` (`DEFAULT_CHUNK_LENGTH_SECONDS`, `DEFAULT_OVERLAP_SECONDS`) or per-call CLI flags `--chunk-length-seconds` / `--overlap-seconds` |
| Accepted audio file types | `.env` (`ACCEPTED_AUDIO_EXTENSIONS`) |
| Output root | `.env` (`OUTPUT_ROOT`) |
| Cache root | `.env` (`CACHE_ROOT`) |
| Budget / hard cap / safety margin | `.env` (`DEFAULT_BUDGET_USD`, `HARD_CAP_USD`, `COST_SAFETY_MARGIN`) |

### Prompt files and backups

The current prompt files used by the pipeline are:

- `src/urdu_pipeline/prompts/transcription_v1.md`
- `src/urdu_pipeline/prompts/reconciliation_v1.md`
- `src/urdu_pipeline/prompts/translation_v1.md`
- `src/urdu_pipeline/prompts/article_v1.md`

The glossary injected into translation lives at:

- `src/urdu_pipeline/prompts/glossary.md`

Backups created during prompt rewrites currently include:

- `src/urdu_pipeline/prompts/transcription_v1.backup_2026-04-30.md`
- `src/urdu_pipeline/prompts/reconciliation_v1.backup_2026-04-30.md`
- `src/urdu_pipeline/prompts/translation_v1.backup_2026-04-30.md`

If you change prompt wording and want reruns to bypass cached outputs, do one
of the following before rerunning:

```bash
# Option 1: edit .env and bump the prompt version
PROMPT_VERSION=v2

# Option 2: remove the existing cache directory
rm -rf .cache_pipeline
```

The safer default is to bump `PROMPT_VERSION`, because it preserves older
cached runs for comparison.

---

## 11. Troubleshooting

### `FFmpegNotFoundError: ffmpeg was not found on PATH`
Install FFmpeg (Section 1b) and reopen your shell so the new `PATH` is
picked up. `which ffmpeg` should print a path.

### `RuntimeError: OPENAI_API_KEY is not set`
You set `PIPELINE_PROVIDER_MODE=real` but did not put a key in `.env`.
Either switch back to `fake` or add the key (Section 6).

### `MissingPricingError: No pricing found for transcription model 'X'`
You changed `TRANSCRIPTION_MODEL` (or another `*_MODEL`) to a value not in
`src/urdu_pipeline/config/pricing.py`. Add an entry to that file with the
current OpenAI price per minute (transcription) or per-million tokens (text).

### `BudgetViolationError: Hard cap exceeded`
The projected total (accumulated + next-stage estimate, multiplied by the
safety margin) is above `HARD_CAP_USD`. Either:
- Bump `HARD_CAP_USD` in `.env` (you must accept the higher cap explicitly),
- Run a shorter audio file, or
- Use cheaper models for translation / article (e.g. `gpt-5.4-mini` or
  `gpt-4o-mini`) and add their pricing entries if missing.

### `ArtifactValidationError: Wrong artifact type`
You uploaded the wrong file into a stage. Double-check the file's
`artifact_type` field against the stage's expected input:
- Transcription expects `chunk_manifest`.
- Reconciliation expects `raw_urdu_transcript`.
- Translation expects `reconciled_urdu_transcript`.
- Article expects `english_translation`.

### Warning: `chunk_NNNN is XX MB, above MAX_CHUNK_MB=24`
A chunk is large enough that OpenAI's transcription endpoint may reject it
(limit is 25 MB). Re-encode your input audio to a lower bitrate / mono, or
reduce `DEFAULT_CHUNK_LENGTH_SECONDS`. The chunker already downsamples
output chunks to 16 kHz / 64 kbps mono MP3, so this usually only happens
with extremely loud / dense source files.

### Streamlit shows "missing ScriptRunContext"
You ran the UI module directly (`python streamlit_app.py`) instead of with
`streamlit run`. Use `streamlit run src/urdu_pipeline/ui/streamlit_app.py`.

### Pip install fails on a particular dependency
1. Confirm `python3 --version` is `>= 3.12`.
2. Run `pip install --upgrade pip setuptools wheel`.
3. On Linux, you may need build dependencies for `tiktoken`:
   `sudo apt install -y build-essential`.

### OpenAI 401 / 429 errors at runtime
- 401: the API key is invalid, expired, or scoped to a different project.
  Regenerate a key at <https://platform.openai.com/api-keys>.
- 429: you hit a rate limit. Lower the request rate, switch to a smaller
  model, or top up account credits.

### Tests fail with `ModuleNotFoundError: urdu_pipeline`
Make sure you installed the package in editable mode
(`pip install -e ".[dev]"`) and that your virtual environment is activated.

---

## 12. Project layout

```
.
├── .env.example
├── .gitignore
├── Makefile
├── README.md
├── pyproject.toml
├── requirements.txt
├── runs/                                   ← per-run outputs (git-ignored)
├── src/
│   └── urdu_pipeline/
│       ├── __init__.py
│       ├── cli.py
│       ├── logging_utils.py
│       ├── artifacts/                      ← store, validators, exporter
│       ├── cache/                          ← cache keys + filesystem cache
│       ├── config/                         ← settings, model roles, pricing
│       ├── costs/                          ← estimator + budget guard
│       ├── prompts/                        ← versioned prompts + glossary
│       │   ├── article_v1.md
│       │   ├── glossary.md
│       │   ├── reconciliation_v1.backup_2026-04-30.md
│       │   ├── reconciliation_v1.md
│       │   ├── transcription_v1.backup_2026-04-30.md
│       │   ├── transcription_v1.md
│       │   ├── translation_v1.backup_2026-04-30.md
│       │   └── translation_v1.md
│       ├── providers/                      ← fake + OpenAI (audio + text)
│       ├── schemas/                        ← Pydantic schemas for artifacts
│       ├── stages/                         ← chunker / transcriber / etc.
│       └── ui/streamlit_app.py
└── tests/
    ├── conftest.py
    ├── integration_safe/                   ← subprocess + end-to-end fake-provider tests
    │   ├── test_fake_pipeline_end_to_end.py
    │   ├── test_makefile.py
    │   └── test_streamlit_import.py
    └── unit/                               ← per-module unit tests
```

---

## 13. Prototype acceptance checklist

The prototype is considered usable when:

- [x] User can upload a 60-minute Urdu audio file.
- [x] App splits it into 5-minute chunks with 60-second overlap.
- [x] App estimates cost before paid API calls.
- [x] App refuses to spend above the hard cap.
- [x] App can transcribe chunks into Urdu script.
- [x] App can reconcile overlap into one Urdu transcript.
- [x] App can translate into American English.
- [x] App can generate a polished American English article.
- [x] User can download every stage output (JSON + Markdown).
- [x] User can upload any prior-stage artifact into the next stage.
- [x] Tests pass without paid API calls.
- [x] Secrets are not committed or logged.

---

## 14. License

MIT.
