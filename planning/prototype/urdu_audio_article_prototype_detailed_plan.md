# Detailed Prototype Development Plan
## Urdu Audio → Urdu Transcript → American English Translation → Standalone Article

Prepared for use with Cursor / Opus-class coding model.

---

## 1. Prototype Goal

Build a **local-first usable prototype** that lets the user upload an Urdu religious lecture audio file and produce:

1. 5-minute audio chunks with 60-second overlap.
2. Raw Urdu-script transcript per chunk.
3. Reconciled Urdu transcript with overlap duplicate removal.
4. American English translation.
5. Polished standalone American English article.
6. Downloadable files at every stage.
7. Manual upload/import into later stages.
8. Cost estimate before paid API calls.
9. Hard block before exceeding `$60`.

This is **not** the production app. The goal is to get something usable quickly without creating sloppy, one-file throwaway code.

---

## 2. Non-Negotiable Defaults

Use these unless the user explicitly changes them later:

| Setting | Default |
|---|---|
| App type | Local Python prototype |
| UI | Streamlit |
| CLI | Typer preferred, argparse acceptable |
| Chunk size | 5 minutes |
| Overlap | 60 seconds |
| Passes per stage | 1 |
| Transcription model | `gpt-4o-transcribe` |
| Translation model | `gpt-5.5` |
| Article model | `gpt-5.5` |
| Reconciliation model | `gpt-5.5`, with deterministic fallback |
| Budget target | Under `$30` |
| Hard cap | `$60` |
| Tests | Fake providers only |
| Storage | Local filesystem |
| Artifacts | JSON + Markdown |
| Secrets | `.env`, never committed |
| Prompt files | Versioned Markdown files |

The prototype must **not** process 3-minute, 10-minute, and 30-minute chunk sets by default. It should process only one chunk set: **5-minute chunks with 60-second overlap**.

---

## 3. Architecture Summary

The app should be organized as a clean local pipeline:

```text
Audio File
  ↓
Chunker
  ↓
Chunk Manifest + Audio Chunks
  ↓
Transcriber
  ↓
Raw Urdu Transcript
  ↓
Transcript Reconciler
  ↓
Clean Urdu Transcript
  ↓
Translator
  ↓
American English Translation
  ↓
Article Generator
  ↓
Standalone American English Article
```

Each stage must be independently runnable from:

1. Streamlit UI.
2. CLI.
3. Existing saved artifact.

The user should be able to stop after any stage, download the output, and later upload that output into the next stage without rerunning earlier paid calls.

---

## 4. Recommended Project Structure

Cursor should create this structure:

```text
urdu-article-prototype/
  README.md
  requirements.txt
  .env.example
  .gitignore
  pyproject.toml

  src/
    urdu_pipeline/
      __init__.py

      cli.py
      logging_utils.py

      config/
        __init__.py
        settings.py
        model_roles.py
        pricing.py

      schemas/
        __init__.py
        base.py
        chunks.py
        transcripts.py
        translations.py
        articles.py
        manifests.py

      prompts/
        glossary.md
        transcription_v1.md
        reconciliation_v1.md
        translation_v1.md
        article_v1.md

      stages/
        __init__.py
        chunker.py
        transcriber.py
        transcript_reconciler.py
        translator.py
        article_generator.py

      providers/
        __init__.py
        base.py
        fake_provider.py
        openai_audio.py
        openai_text.py

      costs/
        __init__.py
        estimator.py
        budget_guard.py

      cache/
        __init__.py
        cache_keys.py
        artifact_cache.py

      artifacts/
        __init__.py
        store.py
        validators.py
        exporter.py

      ui/
        __init__.py
        streamlit_app.py

  tests/
    unit/
      test_config.py
      test_chunker.py
      test_artifacts.py
      test_costs.py
      test_cache.py
      test_transcriber.py
      test_reconciler.py
      test_translator.py
      test_article_generator.py

    integration_safe/
      test_fake_pipeline_end_to_end.py
      test_streamlit_import.py

    fixtures/
      sample_audio_metadata.json
      sample_chunk_manifest.json
      sample_raw_urdu_transcript.json
      sample_reconciled_urdu_transcript.json
      sample_english_translation.json

  runs/
    .gitkeep
```

Do **not** collapse this into one script. Keep it modular enough to later move into a production app.

---

## 5. Environment and Configuration

Create `.env.example`:

```env
OPENAI_API_KEY=

PIPELINE_PROVIDER_MODE=fake

DEFAULT_BUDGET_USD=30
HARD_CAP_USD=60

DEFAULT_CHUNK_LENGTH_SECONDS=300
DEFAULT_OVERLAP_SECONDS=60

TRANSCRIPTION_MODEL=gpt-4o-transcribe
TRANSLATION_MODEL=gpt-5.5
ARTICLE_MODEL=gpt-5.5
RECONCILIATION_MODEL=gpt-5.5

OUTPUT_ROOT=runs
LOG_LEVEL=INFO
```

### Rules

`PIPELINE_PROVIDER_MODE=fake` must require no API key.

`PIPELINE_PROVIDER_MODE=real` must require:

1. `OPENAI_API_KEY` present.
2. Cost estimate shown.
3. User confirmation.
4. Projected cost under selected budget with margin.
5. Projected cost under `$60`.
6. No matching cached artifact already available.

---

## 6. Core Data Model

Use Pydantic schemas.

### 6.1 Base Manifest Schema

Every stage manifest should include:

```python
artifact_id: str
schema_version: str
stage_name: str
created_at: datetime
source_input_hash: str | None
upstream_artifact_ids: list[str]
model_provider: str | None
model_id: str | None
prompt_id: str | None
prompt_version: str | None
chunk_length_seconds: int | None
overlap_seconds: int | None
context_mode: str | None
estimated_cost_usd: float | None
actual_usage: dict | None
cache_hit: bool
checksum: str
warnings: list[str]
human_review_status: str
```

### 6.2 Chunk Schema

```python
chunk_id: str
source_audio_hash: str
chunk_index: int
start_ms: int
end_ms: int
duration_ms: int
overlap_before_ms: int
overlap_after_ms: int
file_path: str
file_hash: str
```

### 6.3 Raw Transcript Chunk Schema

```python
chunk_id: str
chunk_index: int
start_ms: int
end_ms: int
text_urdu: str
uncertainty_markers: list[str]
provider_metadata: dict
```

### 6.4 Reconciled Urdu Segment Schema

```python
segment_id: str
source_chunk_ids: list[str]
approx_start_ms: int | None
approx_end_ms: int | None
text_urdu: str
warnings: list[str]
```

### 6.5 English Translation Segment Schema

```python
segment_id: str
source_segment_id: str
text_english: str
preserved_uncertainty: bool
terminology_notes: list[str]
```

### 6.6 Article Schema

```python
title: str
subtitle: str | None
body_markdown: str
source_translation_artifact_id: str
warnings: list[str]
```

---

## 7. Stage-by-Stage Implementation Plan

### Phase 0 — Repo Setup

Cursor should create:

1. Python package structure.
2. `requirements.txt`.
3. `.env.example`.
4. `.gitignore`.
5. Basic README skeleton.
6. Test folders.
7. `runs/` output folder.

Suggested dependencies:

```txt
openai
pydantic
pydantic-settings
python-dotenv
streamlit
typer
rich
pytest
pytest-mock
tiktoken
python-multipart
```

For audio, use system `ffmpeg` and `ffprobe` through `subprocess`. Avoid adding heavy wrappers unless needed.

Checkpoint output:

```bash
python -m pytest
python -m urdu_pipeline.cli --help
streamlit run src/urdu_pipeline/ui/streamlit_app.py
```

---

### Phase 1 — Config, Model Roles, Pricing, and Safety

Implement:

```text
config/settings.py
config/model_roles.py
config/pricing.py
costs/estimator.py
costs/budget_guard.py
```

#### Required behavior

The app must load config from `.env`.

Model IDs must be centralized in config, not hardcoded inside business logic.

Pricing must be configurable and fail closed. If pricing is missing for a real model, real paid calls should be blocked until the user adds pricing or confirms override.

#### Budget guard rules

Before each paid call:

```text
projected_total = previous_actual_or_estimated_costs + next_stage_estimate
projected_total_with_margin = projected_total * 1.20
```

Allow only if:

```text
projected_total_with_margin <= selected_budget
AND
projected_total_with_margin <= hard_cap
```

If selected budget is `$30`, warn before exceeding it.

If projected total exceeds `$60`, hard block.

---

### Phase 2 — Artifact Store and Validation

Implement:

```text
artifacts/store.py
artifacts/validators.py
artifacts/exporter.py
schemas/*
```

#### Artifact storage layout

For every run:

```text
runs/
  2026-04-27_urdu_lecture_001/
    input/
      original_audio.mp3
      source_metadata.json

    chunks/
      chunk_0001.mp3
      chunk_0002.mp3
      ...

    artifacts/
      chunk_manifest.json
      chunk_summary.md

      raw_urdu_transcript.json
      raw_urdu_transcript.md
      transcript_manifest.json

      reconciled_urdu_transcript.json
      reconciled_urdu_transcript.md
      reconciliation_manifest.json

      english_translation.json
      english_translation.md
      translation_manifest.json

      final_article.json
      final_article.md
      article_manifest.json

    exports/
      full_run_export.zip
```

#### Rules

Artifact validation must reject wrong-stage inputs.

Examples:

| Stage | Accepts | Rejects |
|---|---|---|
| Transcriber | `chunk_manifest.json` | translation/article artifacts |
| Reconciler | `raw_urdu_transcript.json` | final article |
| Translator | `reconciled_urdu_transcript.json/md` | raw chunks |
| Article Generator | `english_translation.json/md` | Urdu transcript |

---

### Phase 3 — Chunker

Implement:

```text
stages/chunker.py
```

#### Behavior

Input:

```text
path/to/audio.mp3
```

Output:

```text
chunks/
chunk_manifest.json
chunk_summary.md
```

Default chunking:

```text
chunk_length = 300 seconds
overlap = 60 seconds
```

Chunk starts should be:

```text
0s
240s
480s
720s
...
```

because each new chunk starts after:

```text
chunk_length - overlap = 240 seconds
```

Each chunk should cover:

```text
start_time → min(start_time + 300s, audio_duration)
```

Final chunk can be shorter.

Use `ffprobe` to determine duration.

Use `ffmpeg` to create chunks.

Important OpenAI API safety: because OpenAI transcription uploads have file-size limits, chunking should check chunk file size and warn if a chunk exceeds the current API limit. Cursor should verify the current limit from official provider documentation before implementation.

#### Chunk manifest fields

```json
{
  "artifact_type": "chunk_manifest",
  "schema_version": "1.0",
  "source_audio_hash": "...",
  "source_audio_duration_ms": 3600000,
  "chunk_length_seconds": 300,
  "overlap_seconds": 60,
  "chunks": [
    {
      "chunk_id": "chunk_0001",
      "chunk_index": 1,
      "start_ms": 0,
      "end_ms": 300000,
      "duration_ms": 300000,
      "file_path": "chunks/chunk_0001.mp3",
      "file_hash": "..."
    }
  ],
  "manifest": {
    "...": "..."
  }
}
```

---

### Phase 4 — Provider Interfaces and Fake Providers

Implement:

```text
providers/base.py
providers/fake_provider.py
providers/openai_audio.py
providers/openai_text.py
```

#### Base interfaces

```python
class AudioTranscriptionProvider:
    def transcribe_chunk(
        self,
        chunk_path: Path,
        prompt: str,
        model_id: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        ...
```

```python
class TextGenerationProvider:
    def generate(
        self,
        prompt: str,
        input_text: str,
        model_id: str,
        response_schema: type | None = None,
    ) -> TextGenerationResult:
        ...
```

#### Fake provider

Fake provider must:

1. Return deterministic Urdu text for transcription tests.
2. Return deterministic reconciled text.
3. Return deterministic English translation.
4. Return deterministic article.
5. Track number of calls so tests can prove cache hits skip provider calls.
6. Never require API keys.

---

### Phase 5 — Transcription Stage

Implement:

```text
stages/transcriber.py
prompts/transcription_v1.md
```

#### Input

```text
chunk_manifest.json
chunks/
```

#### Output

```text
raw_urdu_transcript.json
raw_urdu_transcript.md
transcript_manifest.json
```

#### Transcription prompt requirements

The prompt should say:

```text
You are transcribing Urdu audio. Output Urdu script only.

Do not translate into English.

Preserve spoken English words as spoken.

Preserve Arabic phrases where spoken.

If the audio is unclear, write [غیر واضح].

Do not invent missing words.

This lecture may include Islamic terminology, Arabic phrases, Urdu religious vocabulary, names of scholars, fiqh terms, tasawwuf terms, Qur'anic phrases, and hadith references. Use this awareness only to avoid mishearing technical terms. Do not add religious content that is not clearly present in the audio.

Return only the transcript text for this chunk.
```

#### Context passing

For chunk `n`, pass a short context packet from chunk `n-1`, not the full transcript.

Recommended context packet:

```text
Previous chunk summary:
- Last 3–5 sentences of previous transcript
- Key terms detected so far
- Names/terms glossary
```

Prototype default: include previous chunk tail only, capped to a small character/token limit.

Do **not** pass the full previous chunk by default unless enabled in settings.

---

### Phase 6 — Transcript Reconciliation

Implement:

```text
stages/transcript_reconciler.py
prompts/reconciliation_v1.md
```

#### Purpose

Because chunks overlap by 60 seconds, the raw transcript will contain duplicate content. The reconciler must merge chunk transcripts into a clean Urdu transcript.

#### Approach

Use a two-layer approach:

##### Layer 1: Deterministic overlap cleanup

For each adjacent chunk:

1. Compare the end of chunk `n` with the beginning of chunk `n+1`.
2. Use fuzzy text matching to identify duplicated overlap.
3. Remove obvious duplicates.
4. Preserve uncertain text.

##### Layer 2: Model-assisted cleanup

Use `gpt-5.5` only for final cleanup if needed.

Prompt:

```text
You are merging overlapping Urdu transcript chunks.

Remove duplicated overlap content.

Preserve the best wording.

Do not add new material.

Do not summarize.

Do not translate.

Preserve [غیر واضح] markers.

Keep the transcript in Urdu script.

Return a clean reconciled Urdu transcript.
```

#### Output

```text
reconciled_urdu_transcript.json
reconciled_urdu_transcript.md
reconciliation_manifest.json
```

---

### Phase 7 — Translation Stage

Implement:

```text
stages/translator.py
prompts/translation_v1.md
prompts/glossary.md
```

#### Input

```text
reconciled_urdu_transcript.json
```

or:

```text
reconciled_urdu_transcript.md
```

#### Output

```text
english_translation.json
english_translation.md
translation_manifest.json
```

#### Translation rules

The translation should:

1. Be American English.
2. Preserve meaning and nuance.
3. Preserve religious terminology carefully.
4. Use English translation first, then italicized transliteration.
5. Preserve uncertainty markers.
6. Avoid summarizing.
7. Avoid adding claims not in the Urdu transcript.
8. Preserve the speaker’s intended argument.

Example style:

```text
humility (*tawāḍuʿ*)
rejecting the truth (*baṭar al-ḥaqq*)
sincerity (*ikhlāṣ*)
```

#### Chunking long translation inputs

If the reconciled transcript is too long for one model call:

1. Split by segment boundaries.
2. Carry forward a small terminology packet.
3. Translate sequentially.
4. Merge into one Markdown file.

The terminology packet should include:

```text
Known terms:
- Allāh
- ḥadīth
- sunnah
- sharīʿah
- fiqh
- Ḥanafī
- Māturīdī
- taṣawwuf
- ʿulamāʾ
```

Do not use the article-generation prompt here. This stage is translation, not rewriting.

---

### Phase 8 — Article Generation Stage

Implement:

```text
stages/article_generator.py
prompts/article_v1.md
```

#### Input

```text
english_translation.json
```

or:

```text
english_translation.md
```

#### Output

```text
final_article.json
final_article.md
article_manifest.json
```

#### Article prompt requirements

The article should:

1. Be a cohesive standalone American English article.
2. Target about an 8th-grade reading level.
3. Have a relevant title.
4. Use logical headings.
5. Remove transcript-like filler.
6. Preserve religious dignity.
7. Preserve theological nuance.
8. Avoid hallucinating new claims.
9. Avoid sounding like a transcript.
10. Preserve uncertainty when source text is unclear.

Prompt skeleton:

```text
You are writing a standalone American English article from a translated Urdu religious lecture.

Use the English translation as your only source.

Do not introduce new claims, stories, hadith, Qur'anic references, names, or theological points unless they are present in the source translation.

Write clearly at about an 8th-grade reading level.

Maintain dignity and religious seriousness.

Use headings and paragraph breaks.

Remove filler, repetitions, and conversational artifacts.

Preserve important religious terminology using English first, followed by italicized transliteration where appropriate.

If the source includes uncertainty markers, do not hide them. Either preserve them or phrase cautiously.

Return Markdown.
```

---

## 8. Cache System

Implement:

```text
cache/cache_keys.py
cache/artifact_cache.py
```

Cache keys must include:

```text
input_hash
stage_name
model_provider
model_id
prompt_version
chunk_length_seconds
overlap_seconds
context_mode
model_parameters
```

### Behavior

If same input + same config + same prompt version already exists:

1. Do not call provider.
2. Return cached artifact.
3. Mark manifest:

```json
"cache_hit": true
```

Tests must prove that fake provider call count does not increase on cache hit.

---

## 9. CLI Plan

Implement:

```text
src/urdu_pipeline/cli.py
```

Use Typer if possible.

Required commands:

```bash
python -m urdu_pipeline.cli estimate --audio path/to/audio.mp3
```

```bash
python -m urdu_pipeline.cli chunk \
  --audio path/to/audio.mp3 \
  --out runs/job_id
```

```bash
python -m urdu_pipeline.cli transcribe \
  --chunk-manifest runs/job_id/artifacts/chunk_manifest.json
```

```bash
python -m urdu_pipeline.cli reconcile \
  --transcript runs/job_id/artifacts/raw_urdu_transcript.json
```

```bash
python -m urdu_pipeline.cli translate \
  --transcript runs/job_id/artifacts/reconciled_urdu_transcript.json
```

```bash
python -m urdu_pipeline.cli article \
  --translation runs/job_id/artifacts/english_translation.json
```

```bash
python -m urdu_pipeline.cli run-all \
  --audio path/to/audio.mp3 \
  --budget 30
```

```bash
python -m urdu_pipeline.cli validate-artifact \
  --artifact path/to/artifact.json
```

```bash
python -m urdu_pipeline.cli export-run \
  --run-dir runs/job_id
```

### CLI safety

For real provider mode, CLI should require:

```bash
--confirm-paid-run
```

or interactive confirmation.

Without confirmation, it should print estimate and stop.

---

## 10. Streamlit UI Plan

Implement:

```text
ui/streamlit_app.py
```

### Required tabs

#### Tab 1: Full Pipeline

Fields:

1. Upload audio.
2. Show duration.
3. Show estimated cost.
4. Show selected models.
5. Show chunk length = 5 minutes.
6. Show overlap = 60 seconds.
7. Select provider mode: fake/real.
8. Budget input.
9. Confirm paid API calls.
10. Run full pipeline.
11. Show progress by stage.
12. Download all outputs.

#### Tab 2: Chunker

1. Upload audio.
2. Run chunking.
3. Download:
   - `chunk_manifest.json`
   - `chunk_summary.md`
   - chunks ZIP.

#### Tab 3: Transcription

1. Upload chunk manifest ZIP or select current run.
2. Validate artifact.
3. Estimate transcription cost.
4. Run transcription.
5. Download:
   - `raw_urdu_transcript.json`
   - `raw_urdu_transcript.md`
   - `transcript_manifest.json`.

#### Tab 4: Reconciliation

1. Upload raw Urdu transcript artifact.
2. Validate artifact.
3. Run reconciliation.
4. Download:
   - `reconciled_urdu_transcript.json`
   - `reconciled_urdu_transcript.md`
   - `reconciliation_manifest.json`.

#### Tab 5: Translation

1. Upload reconciled Urdu transcript.
2. Validate artifact.
3. Estimate text-model cost.
4. Run translation.
5. Download:
   - `english_translation.json`
   - `english_translation.md`
   - `translation_manifest.json`.

#### Tab 6: Article

1. Upload English translation.
2. Validate artifact.
3. Estimate article cost.
4. Generate article.
5. Download:
   - `final_article.json`
   - `final_article.md`
   - `article_manifest.json`.

#### Tab 7: Settings

Show/edit:

1. Provider mode.
2. Model roles.
3. Budget.
4. Hard cap.
5. Chunk length.
6. Overlap.
7. Output directory.
8. API key status only, never key value.
9. Prompt version.
10. Dry-run mode.

---

## 11. Prompt Files

Create these files:

```text
prompts/glossary.md
prompts/transcription_v1.md
prompts/reconciliation_v1.md
prompts/translation_v1.md
prompts/article_v1.md
```

### `glossary.md`

Initial content:

```md
# Glossary

Use these terms for awareness and consistency only. Do not force them if they are not present.

- Allāh
- ṣallā Allāhu ʿalayhi wa-sallam
- raḥmatullāhi ʿalayh
- nawwara Allāhu marqadahu
- taqwā
- tawāḍuʿ
- ḥadīth
- sunnah
- sharīʿah
- fiqh
- Māturīdī
- Ḥanafī
- taṣawwuf
- ʿulamāʾ
- ikhlāṣ
- adab
- nafs
- dhikr
- ṣuḥbah
```

---

## 12. Testing Plan

Tests must be written before implementation for each core module.

No tests may call real OpenAI APIs.

### 12.1 Config Tests

File:

```text
tests/unit/test_config.py
```

Test:

1. `.env` loads.
2. fake mode works without API key.
3. real mode fails without API key.
4. model roles resolve from config.
5. hard cap defaults to `$60`.

### 12.2 Chunker Tests

File:

```text
tests/unit/test_chunker.py
```

Test:

1. 60-minute file produces expected chunk count.
2. Chunks start every 240 seconds.
3. Each normal chunk is 300 seconds.
4. Final chunk may be shorter.
5. Manifest includes source hash.
6. Unsafe file names are sanitized.
7. Chunk paths stay inside run directory.

### 12.3 Artifact Tests

File:

```text
tests/unit/test_artifacts.py
```

Test:

1. Manifest includes required fields.
2. Checksum validation works.
3. Wrong-stage artifact rejected.
4. Correct artifact accepted by next stage.
5. Export ZIP includes safe artifacts only.

### 12.4 Cost Tests

File:

```text
tests/unit/test_costs.py
```

Test:

1. Transcription minute estimate works.
2. Token estimate works.
3. 20% safety margin is applied.
4. Budget warning works.
5. `$60` hard stop works.
6. Missing pricing blocks real mode.

### 12.5 Cache Tests

File:

```text
tests/unit/test_cache.py
```

Test:

1. Same input/config gives same cache key.
2. Changed model changes cache key.
3. Changed prompt version changes cache key.
4. Cache hit skips provider call.

### 12.6 Transcription Tests

File:

```text
tests/unit/test_transcriber.py
```

Test:

1. Fake provider returns Urdu-script output.
2. Transcriber saves JSON.
3. Transcriber saves Markdown.
4. Raw transcript includes chunk references.
5. Real provider is not called.

### 12.7 Reconciliation Tests

File:

```text
tests/unit/test_reconciler.py
```

Test:

1. Simple duplicate overlap is removed.
2. `[غیر واضح]` is preserved.
3. Source chunk references are preserved.
4. Output remains Urdu script.

### 12.8 Translation Tests

File:

```text
tests/unit/test_translator.py
```

Test:

1. Fake provider returns English translation.
2. Translator accepts reconciled Urdu transcript.
3. Translator rejects raw chunk manifest.
4. Uncertainty markers preserved.

### 12.9 Article Tests

File:

```text
tests/unit/test_article_generator.py
```

Test:

1. Article generator accepts English translation.
2. Article output includes title.
3. Article output includes body paragraphs.
4. Article generator rejects Urdu transcript artifact.

### 12.10 Safe Integration Tests

File:

```text
tests/integration_safe/test_fake_pipeline_end_to_end.py
```

Test:

1. Full fake pipeline runs end-to-end.
2. Every stage produces JSON + Markdown.
3. Export ZIP is created.
4. No API key required.

File:

```text
tests/integration_safe/test_streamlit_import.py
```

Test:

1. Streamlit app imports.
2. Main pipeline functions are callable.

---

## 13. Implementation Checkpoints for Cursor

Cursor should implement in these batches.

### Checkpoint 1 — Skeleton, Config, Schemas, Fake Providers

Deliver:

1. Project skeleton.
2. Settings loader.
3. Model roles.
4. Pricing config.
5. Base Pydantic schemas.
6. Fake providers.
7. Initial tests.

Cursor should stop and show:

```text
Files changed
Tests added
How to run tests
Known limitations
Next checkpoint
```

Do not call real APIs.

---

### Checkpoint 2 — Artifact Store, Budget Guard, Cache, Chunker

Deliver:

1. Artifact store.
2. Checksum validation.
3. Exporter.
4. Cost estimator.
5. Budget guard.
6. Cache key logic.
7. Chunker using ffmpeg/ffprobe.
8. Chunk manifest generation.
9. Tests.

Checkpoint command:

```bash
python -m pytest tests/unit/test_chunker.py tests/unit/test_artifacts.py tests/unit/test_costs.py tests/unit/test_cache.py
```

---

### Checkpoint 3 — Transcription Stage

Deliver:

1. Transcription prompt.
2. Transcriber stage.
3. Fake transcription flow.
4. OpenAI transcription provider behind config.
5. Transcription JSON/Markdown outputs.
6. Context packet from previous chunk tail.
7. Tests.

Important: OpenAI real provider code must support the current transcription endpoint. Cursor must verify endpoint and model support in the official documentation before implementation.

Do not execute real provider in tests.

---

### Checkpoint 4 — Reconciliation, Translation, Article Generation

Deliver:

1. Reconciliation stage.
2. Translation stage.
3. Article generation stage.
4. Prompt files.
5. JSON/Markdown outputs.
6. Tests for all three stages.

Checkpoint command:

```bash
python -m pytest tests/unit/test_reconciler.py tests/unit/test_translator.py tests/unit/test_article_generator.py
```

---

### Checkpoint 5 — CLI

Deliver all CLI commands:

```bash
estimate
chunk
transcribe
reconcile
translate
article
run-all
validate-artifact
export-run
```

CLI must support fake mode and real mode.

Real mode must require explicit paid-run confirmation.

---

### Checkpoint 6 — Streamlit UI

Deliver tabs:

1. Full Pipeline.
2. Chunker.
3. Transcription.
4. Reconciliation.
5. Translation.
6. Article.
7. Settings.

UI must expose downloads for every stage.

UI must never show API key.

UI must show estimated cost before paid calls.

---

### Checkpoint 7 — End-to-End Fake Run and README

Deliver:

1. End-to-end fake provider integration test.
2. README with setup instructions.
3. Troubleshooting.
4. Known limitations.
5. Migration notes for future production app.

Final test command:

```bash
python -m pytest
```

Final app command:

```bash
streamlit run src/urdu_pipeline/ui/streamlit_app.py
```

---

## 14. README Requirements

The README must include:

1. What this prototype does.
2. What it does not do yet.
3. How to install `ffmpeg`.
4. How to create a virtual environment.
5. How to install dependencies.
6. How to copy `.env.example` to `.env`.
7. How to run tests.
8. How to run with fake providers.
9. How to run with real OpenAI providers.
10. How to launch Streamlit.
11. How to use the CLI.
12. Where outputs are saved.
13. How to resume from artifacts.
14. How budget controls work.
15. How to change model IDs.
16. How to change prompts.
17. Common errors:
    - missing `ffmpeg`
    - missing API key
    - chunk too large
    - wrong artifact uploaded
    - budget cap exceeded
    - model pricing missing

---

## 15. Exact Local Run Commands

### Create environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure environment

```bash
cp .env.example .env
```

For fake mode:

```env
PIPELINE_PROVIDER_MODE=fake
```

For real mode:

```env
PIPELINE_PROVIDER_MODE=real
OPENAI_API_KEY=your_key_here
```

### Run tests

```bash
python -m pytest
```

### Run Streamlit

```bash
streamlit run src/urdu_pipeline/ui/streamlit_app.py
```

### Run full fake pipeline

```bash
python -m urdu_pipeline.cli run-all \
  --audio path/to/audio.mp3 \
  --budget 30 \
  --provider-mode fake
```

### Estimate real run

```bash
python -m urdu_pipeline.cli estimate \
  --audio path/to/audio.mp3
```

### Run real pipeline after estimate

```bash
python -m urdu_pipeline.cli run-all \
  --audio path/to/audio.mp3 \
  --budget 30 \
  --provider-mode real \
  --confirm-paid-run
```

---

## 16. Accuracy Strategy

The prototype should preserve accuracy by doing this:

1. Chunk audio into 5-minute chunks with 60-second overlap.
2. Use Urdu-script transcription.
3. Pass a short previous-chunk context packet into the next transcription call.
4. Preserve uncertainty as `[غیر واضح]`.
5. Reconcile overlapping transcript before translation.
6. Translate the reconciled Urdu transcript, not raw chunks.
7. Generate article only from the English translation.
8. Keep glossary editable.
9. Never let article generation invent details.
10. Keep all artifacts available for manual review.

---

## 17. Known Prototype Limitations

These are acceptable for version 1:

1. No diarization by default.
2. No word-level timestamps by default.
3. No multi-provider comparison.
4. No production database.
5. No user accounts.
6. No background queue.
7. No cloud deployment.
8. No advanced human review dashboard.
9. No multi-pass refinement by default.
10. No automatic theological fact-checking.

---

## 18. Future Upgrade Path

After the prototype works, future versions can add:

1. Multiple chunk sizes.
2. Multi-pass transcription review.
3. Optional diarization.
4. More precise timestamps.
5. Batch API support for cheaper non-urgent processing.
6. Better translation memory.
7. Human review UI.
8. Provider comparison.
9. Cloud deployment.
10. Production database.
11. Job queue.
12. Auth.
13. Team workflow.
14. Export to DOCX/PDF.
15. Audio-player transcript alignment.

---

## 19. Cursor Prompt to Use

Paste this into Cursor after attaching the two source files:

```text
You are building a fast local prototype for an Urdu audio transcription, Urdu-to-English translation, and standalone American English article generation app.

Use the uploaded context file and prototype instruction file as the source of truth.

Create the prototype in Python 3.12+ with Streamlit, CLI support, Pydantic schemas, local filesystem artifacts, fake providers for tests, and OpenAI providers behind configuration.

Do not build a full production app.

Non-negotiables:
- One chunk size by default: 5 minutes.
- 60-second overlap.
- One AI pass per stage by default.
- Transcription model configurable, default gpt-4o-transcribe.
- Translation model configurable, default gpt-5.5.
- Article model configurable, default gpt-5.5.
- Budget target under $30.
- Hard cap $60.
- No paid API calls in tests.
- Fake providers for all tests.
- Downloadable JSON and Markdown artifacts at every stage.
- Manual upload/import into the next stage.
- Prompt files must be versioned Markdown files.
- Model IDs and pricing must be centralized in config.
- Secrets must never be committed or logged.

First output:
1. Architecture summary.
2. Exact project structure.
3. Implementation checkpoints.
4. Test plan.
5. Run commands.
6. Assumptions.
7. Ask for permission to begin implementation.

Then implement in checkpoints:
1. Skeleton, config, schemas, fake providers.
2. Artifact store, budget guard, cache, chunker.
3. Transcription stage with fake provider and real OpenAI provider behind config.
4. Reconciliation, translation, article stages.
5. CLI.
6. Streamlit UI.
7. End-to-end fake-provider test and README.

Before implementing real API calls, verify current OpenAI model IDs, endpoint requirements, upload limits, response formats, and pricing from official OpenAI docs.

Never run paid API calls during tests.
Never log secrets.
Never log raw transcripts by default.
```

---

## 20. Acceptance Criteria

The prototype is done when:

1. User can upload a 60-minute Urdu audio file.
2. App creates 5-minute chunks with 60-second overlap.
3. App estimates cost before paid calls.
4. App blocks any run projected above `$60`.
5. App transcribes chunks into Urdu script.
6. App saves raw Urdu transcript JSON and Markdown.
7. App reconciles overlap into one Urdu transcript.
8. App translates into American English.
9. App generates polished standalone American English article.
10. User can download every stage output.
11. User can upload any prior-stage artifact into the next stage.
12. Tests pass without paid API calls.
13. README explains setup and usage.
14. Secrets are not committed or logged.
15. Code is modular enough to reuse in a later production system.
