# Cloud-Agnostic API Conversion Progress Handoff

Last updated: 2026-06-07

This document summarizes what has been completed from
`cloud_agnostic_api_conversion_stepwise_commit_plan.md`, why each part exists,
what files/architecture were affected, what is still left, and which remaining
work is actually needed depending on the real goal.

## Critical Clarification

The stepwise plan is for a full backend conversion:

- local CLI prototype
- provider-neutral stage boundaries
- durable PostgreSQL metadata
- S3-compatible object storage
- Redis/Valkey job delivery
- authenticated API
- separate processor runtime
- local Docker parity
- operational hardening
- Cloudflare/R2 adapter spike
- production deployment

That is much broader than "switch to a different AI provider."

If the immediate goal is only to switch AI providers, then most of Phase 3 and
all of Stages 4-9 are not required right now. The essential provider-switch
foundation is mostly in Stage 2:

- provider request objects
- provider-neutral source data separation
- stage boundaries that accept provider ports
- prompt-injection fixtures
- model-role/provider-config hooks

Phase 3 and later are needed only if the product goal is to turn the local CLI
prototype into a durable, multi-user, API-backed, asynchronous processing
system. They are not required to add another text/transcription provider for
the existing CLI flow.

Recommended decision point:

- If the priority is "finish my work by switching AI providers", pause the
  cloud/API plan after the completed Stage 2 work and implement the new provider
  adapter(s).
- If the priority is "ship the backend API/processor architecture", continue
  the plan from Step 3.3.3.

## Current Implementation Status

Completed through:

- Stage 0
- Stage 1
- Stage 2
- Stage 3 through Step 3.3.2

Next step in the original plan:

- Step 3.3.3: Implement Local Secrets And Scoped Cache Store

Most recent verification:

- Unit tests: `186 passed`
- Safe integration tests: `15 passed, 3 skipped`
- Skipped live-service smokes:
  - PostgreSQL smoke, guarded by `RUN_POSTGRES_MIGRATION_SMOKE=1`
  - MinIO/S3 smoke, guarded by `RUN_MINIO_OBJECT_STORE_SMOKE=1`
  - Redis smoke, guarded by `RUN_REDIS_JOB_QUEUE_SMOKE=1`

The live smokes are skipped by default so normal local/offline tests do not
require Docker services or network dependencies.

## What Was Completed And Why

## Stage 0: Decisions, Dependency Split, And Local Skeleton

Purpose:

Establish the architecture decisions and repository shape before changing
runtime behavior. This stage prevents later steps from mixing incompatible
assumptions about where state lives, who runs trusted code, and which
dependencies are required in which runtime.

### Phase 0.1: Architecture Decisions

### Step 0.1.1: Add ADR For Canonical Runtime Shape

What was done:

- Added ADR documenting the canonical runtime shape.
- The architecture is API plus processor rather than one monolithic app.

Why it was needed:

- A backend API should accept requests, authenticate users, issue upload URLs,
  create runs/jobs, and expose artifacts/events.
- A processor should do expensive/transient work such as downloading audio,
  calling model providers, and writing artifacts.
- This split protects the API from long-running jobs and makes retries,
  cancellation, and job leases possible.

Needed for provider switch only?

- Not strictly. Provider switching can happen inside the current CLI.
- It is needed for a future hosted backend.

### Step 0.1.2: Add ADR For Processor Trust Boundary

What was done:

- Added ADR describing the processor as trusted worker code.
- User input and model output remain untrusted data.

Why it was needed:

- Model prompts can contain prompt-injection payloads from source text.
- The processor must not treat model/source text as instructions.
- Later stage refactors use structured provider request objects to enforce this.

Needed for provider switch only?

- Yes, indirectly. Any provider switch must preserve this boundary.

### Step 0.1.3: Add ADR For Opaque Object Keys

What was done:

- Added ADR requiring opaque object keys and no user-controlled path segments.

Why it was needed:

- Uploaded files and artifacts will eventually live in object storage.
- Object keys must not leak filenames, prompts, model names, or user-controlled
  path fragments.
- It prevents path traversal and cross-user object confusion.

Needed for provider switch only?

- Not for provider switching alone.
- Needed for hosted object storage.

### Phase 0.2: Dependency And Module Skeleton

### Step 0.2.1: Split Optional Dependencies

What was done:

- Split dependencies into runtime extras such as `cli`, `ui`, `processor`,
  `db`, `object-store`, and `queue`.
- Added tests that core imports do not require heavy optional packages.

Why it was needed:

- API, processor, CLI, UI, database, object store, and queue should not all
  require every package.
- This keeps lightweight imports safe and avoids accidental runtime coupling.

Needed for provider switch only?

- Helpful but not mandatory.
- Provider adapters should live behind optional runtime dependencies.

### Step 0.2.2: Add No-Op Module Skeleton

What was done:

- Added/import-tested package skeletons for:
  - `urdu_pipeline.domain`
  - `urdu_pipeline.application`
  - `urdu_pipeline.infrastructure`
  - `urdu_pipeline.api`
  - `urdu_pipeline.processor`

Why it was needed:

- Creates stable ownership boundaries before adding real adapters/services.

Needed for provider switch only?

- Not required, but it gives a clean place for provider-neutral ports/adapters.

### Phase 0.3: Local Stack Skeleton

### Step 0.3.1: Add Docker Compose Skeleton

What was done:

- Added local compose skeleton for API, processor, PostgreSQL, MinIO, Redis, and
  reverse proxy.

Why it was needed:

- The plan targets local parity with cloud-like services before production.
- Later live smoke tests can run against these services.

Needed for provider switch only?

- No.

### Step 0.3.2: Add Make Targets For Local Stack

What was done:

- Added Makefile orchestration targets.
- Added tests around Makefile behavior and placeholder local stack targets.

Why it was needed:

- Provides repeatable developer commands and verifies CLI compatibility.

Needed for provider switch only?

- Mostly no, except existing CLI/Make tests protect against regressions.

## Stage 1: Core Domain, Ports, And Contract Tests

Purpose:

Define provider-neutral application concepts before implementing adapters.
Ports make it possible to swap implementations: in-memory for tests,
filesystem for CLI compatibility, PostgreSQL/S3/Redis for backend mode.

### Phase 1.1: Domain IDs And States

### Step 1.1.1: Add Domain ID Types And Builders

What was done:

- Added strict prefixed IDs:
  - `UserId`
  - `UploadId`
  - `RunId`
  - `JobId`
  - `ArtifactId`
  - `ProviderConfigVersionId`
  - `ProviderRunId`
  - `ServiceIdentityId`
  - `CleanupTaskId`

Why it was needed:

- IDs are opaque, server-generated, and safe to expose.
- Avoids using filenames, object keys, or user-provided strings as identifiers.

Needed for provider switch only?

- Not required for a simple provider adapter.
- Required for API/backend ownership and persistence.

### Step 1.1.2: Add State Enums

What was done:

- Added stable persisted states for uploads, runs, jobs, attempts, artifacts,
  provider configs, cleanup tasks, users, and service identities.

Why it was needed:

- DB rows, API responses, and processor logic need shared state vocabulary.
- Avoids ad hoc strings scattered through the code.

Needed for provider switch only?

- Not directly, unless provider config is persisted.

### Phase 1.2: Ports

### Step 1.2.1: Add Storage And Workspace Ports

What was done:

- Added ports for:
  - object store
  - run workspace
  - artifact sink
  - artifact repository

Why it was needed:

- Separates local scratch files from durable artifact storage.
- Allows filesystem now and S3/R2 later without changing stage logic.

Needed for provider switch only?

- Artifact sink/workspace ports are useful but not mandatory.

### Step 1.2.2: Add Metadata, Queue, Cache, Auth, Secrets, Provider, And Usage Ports

What was done:

- Added ports for:
  - metadata store
  - job queue
  - cache store
  - auth service
  - secret provider
  - provider registry
  - usage ledger
  - budget service

Why it was needed:

- These are the abstractions that make API/processor/cloud deployment possible.
- The provider registry and usage ledger are especially relevant to provider
  switching and cost tracking.

Needed for provider switch only?

- Provider registry and usage ledger are relevant.
- Auth, queue, metadata, and secrets are backend concerns.

### Phase 1.3: Object Keys And In-Memory Adapters

### Step 1.3.1: Add Opaque Object-Key Builder

What was done:

- Added object-key builder with tests for safe, opaque path layouts.

Why it was needed:

- Prevents object-storage keys from containing unsafe or user-controlled paths.

Needed for provider switch only?

- No.

### Step 1.3.2: Add In-Memory Object And Metadata Adapters

What was done:

- Added in-memory object store and metadata store.

Why it was needed:

- Enables fast contract tests without real infrastructure.
- Establishes adapter behavior before durable implementations.

Needed for provider switch only?

- Useful for tests, not required for provider adapter implementation.

### Step 1.3.3: Add In-Memory Queue, Cache, Secrets, Provider Registry, And Usage Adapters

What was done:

- Added in-memory implementations for queue, cache, secrets, provider registry,
  usage ledger, and budget service.

Why it was needed:

- Gives tests and local code a provider-neutral way to exercise these ports.

Needed for provider switch only?

- Provider registry and usage ledger are useful.
- Queue/secrets/cache become important only for backend mode.

## Stage 2: Stage Boundary And Prompt-Safety Refactor

Purpose:

Make pipeline stages provider-neutral and prompt-injection resistant while
preserving the existing CLI behavior.

This is the most relevant completed stage for switching AI providers.

### Phase 2.1: Filesystem Compatibility

### Step 2.1.1: Add Filesystem Workspace And Artifact Sink Adapters

What was done:

- Added filesystem workspace and artifact sink adapters around existing run
  directory behavior.

Why it was needed:

- Lets stages write through a port while preserving current local artifact
  layout.

Provider-switch relevance:

- Helpful because provider work can be tested without changing file layout.

### Step 2.1.2: Add Filesystem Cache Adapter

What was done:

- Added filesystem-backed cache store adapter over existing artifact cache.

Why it was needed:

- Preserves local cache behavior while moving stages toward cache ports.

Provider-switch relevance:

- Useful for avoiding repeated paid provider calls.

### Phase 2.2: Provider Request Objects And Prompt Tests

### Step 2.2.1: Add Provider Request Models

What was done:

- Added structured provider request objects for text and audio calls.
- Added source-data and prompt-metadata wrappers.

Why it was needed:

- Provider calls should be explicit about:
  - model ID
  - developer/system instructions
  - untrusted source data
  - schema/output instructions
  - stage/prompt metadata

Provider-switch relevance:

- Essential. A new provider adapter should consume these request objects.

### Step 2.2.2: Add Prompt-Injection Fixtures

What was done:

- Added tests with malicious/prompt-injection-like source text.

Why it was needed:

- Proves source text is not interpolated into trusted instructions.
- Prevents regressions while refactoring every stage.

Provider-switch relevance:

- Essential. Provider adapters must preserve untrusted source-data separation.

### Phase 2.3: Refactor Stages One At A Time

### Step 2.3.1: Refactor Chunker Stage Boundary

What was done:

- Chunker now uses workspace/artifact sink boundaries while preserving CLI
  layout.

Why it was needed:

- Chunking is local work but must later fit processor/workspace behavior.

Provider-switch relevance:

- Low.

### Step 2.3.2: Refactor Urdu Transcriber Stage Boundary

What was done:

- Urdu transcription stage now uses provider request objects, cache, usage, and
  workspace/artifact sink ports.

Why it was needed:

- Transcription provider calls became adapter-friendly.

Provider-switch relevance:

- Essential if switching transcription/ASR provider.

### Step 2.3.3: Refactor English AM Transcriber Stage Boundary

What was done:

- English American transcription path received the same boundary treatment.

Why it was needed:

- Keeps standalone English transcription consistent with the main stage.

Provider-switch relevance:

- Relevant only if this stage is still used.

### Step 2.3.4: Refactor Reconciler Stage Boundary

What was done:

- Reconciler now writes via artifact sink and preserves local compatibility.

Why it was needed:

- Keeps non-provider stage inside the same adapter pattern.

Provider-switch relevance:

- Low.

### Step 2.3.5: Refactor Translator Prompt And Stage Boundary

What was done:

- Translator now sends source transcript as fenced/untrusted source data.
- Uses provider request object, provider config, cache store, artifact sink, and
  usage ledger.

Why it was needed:

- Translation is high prompt-injection risk.
- Provider adapter should receive structured instructions and source data.

Provider-switch relevance:

- Essential for switching text generation/translation provider.

### Step 2.3.6: Refactor Article Generator Prompt And Stage Boundary

What was done:

- Article generator now sends source translation as fenced/untrusted source
  data.
- Uses provider request object, schema instructions, provider config, cache
  store, artifact sink, and usage ledger.

Why it was needed:

- Article generation consumes potentially adversarial source translation.
- Structured output/schema instructions are now separated from source text.

Provider-switch relevance:

- Essential for switching article-generation provider.

### Step 2.3.7: Run Full CLI Compatibility Pass

What was done:

- Ran unit, safe integration, and Makefile orchestration tests.
- No compatibility fixes were needed.

Why it was needed:

- Confirms Stage 2 refactors did not break existing CLI workflow.

Provider-switch relevance:

- Important because it proves the local CLI still works after provider-boundary
  changes.

## Stage 3: Local Persistence Adapters

Purpose:

Build a production-like local backend foundation using PostgreSQL, S3-compatible
object storage, and Redis/Valkey.

This stage is not needed merely to switch AI providers for the current CLI.
It is needed for an API/processor product where jobs survive restarts, users own
data, uploads go to object storage, queues deliver work, and usage/cost history
is durable.

### Phase 3.1: Database Schema And Migrations

### Step 3.1.1: Add Migration Framework

What was done:

- Added SQL migration runner.
- Added bootstrap `schema_migrations` migration.
- Added `migrate-db` CLI/Make target.
- Added `DATABASE_URL` config.
- Added optional PostgreSQL smoke test.

Why it was needed:

- Durable backend state needs repeatable schema creation.
- Future API/processor services need a known DB schema.

Provider-switch relevance:

- Not required for provider switching unless provider config/usage must be
  persisted.

### Step 3.1.2: Add Core User/Auth/Upload/Run Tables

What was done:

- Added tables:
  - `users`
  - `sessions`
  - `api_tokens`
  - `service_identities`
  - `uploads`
  - `runs`

Why it was needed:

- API mode needs users, auth/session/token records, uploads, and runs.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 3.1.3: Add Job, Attempt, Artifact, Event, Usage, Cache, Provider Config, Prompt, And Cleanup Tables

What was done:

- Added tables:
  - `jobs`
  - `job_attempts`
  - `artifacts`
  - `artifact_document_chunks`
  - `stage_events`
  - `provider_runs`
  - `usage_ledger`
  - `cache_entries`
  - `provider_config_versions`
  - `provider_config_entries`
  - `prompt_versions`
  - `cleanup_tasks`

Why it was needed:

- Processor mode needs leases, attempts, artifacts, events, usage/cost ledger,
  cache, provider config snapshots, prompts, and cleanup tasks.

Provider-switch relevance:

- Provider config and usage ledger are relevant.
- Job/artifact/event/cleanup tables are backend infrastructure, not required
  for a simple provider adapter.

### Phase 3.2: PostgreSQL Metadata Store

### Step 3.2.1: Implement User/Auth/Upload/Run Metadata Methods

What was done:

- Added `PostgresMetadataStore` for users, service identities, uploads, and
  runs.
- Added owned reads and user-scoped upload/run lists.
- Added transaction commit/rollback behavior.

Why it was needed:

- API needs durable user/run/upload metadata.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 3.2.2: Implement Job Lease And State Metadata Methods

What was done:

- Implemented persisted job create/read.
- Implemented compare-and-set claim.
- Implemented lease extension.
- Implemented expired lease reclaim.
- Implemented retry, cancellation, terminal failure, and dead-letter
  transitions.

Why it was needed:

- Processor workers need authoritative job state in PostgreSQL.
- Redis/Valkey delivery cannot be trusted as source of truth.

Provider-switch relevance:

- Not required for simple provider switching.
- Required for asynchronous backend processing.

### Step 3.2.3: Implement Artifact, Event, Usage, Cache, Provider Config, Prompt, And Cleanup Metadata Methods

What was done:

- Implemented:
  - artifact records
  - artifact document chunks below 256 KB
  - stage events
  - provider config snapshots
  - prompt versions
  - usage reservations/releases/actual costs
  - scoped cache entries
  - idempotent cleanup tasks

Why it was needed:

- API/processor backend needs durable artifacts/events/config/prompts/cache and
  usage accounting.

Provider-switch relevance:

- Provider config and usage ledger are directly useful.
- Artifact/event/cache/cleanup persistence are backend features.

### Phase 3.3: Object Store, Queue, Secrets, Cache, And Seeds

### Step 3.3.1: Implement MinIO/S3 Object Store

What was done:

- Added lazy-boto3 `S3ObjectStore`.
- Supports:
  - put/get/head/delete
  - list/delete prefix
  - metadata mapping
  - signed upload/download URLs
  - multipart create/sign/complete/abort
- Added optional MinIO smoke test.
- Added `object-store` optional dependency extra.

Why it was needed:

- Hosted backend uploads/artifacts should live in S3-compatible storage rather
  than local run directories.
- MinIO provides local parity for S3/R2-like services.

Provider-switch relevance:

- Not required for provider switching.

### Step 3.3.2: Implement Redis/Valkey Job Queue

What was done:

- Added lazy-redis `RedisJobQueue`.
- Redis/Valkey is used only for delivery hints.
- PostgreSQL job state remains authoritative.
- Duplicate/stale Redis messages are skipped if persisted job state does not
  allow claim.
- Retry requeues after metadata state changes.
- Added optional Redis smoke test.
- Added `queue` optional dependency extra.

Why it was needed:

- Backend processor workers need a queue for job delivery.
- Persisted DB state must remain authoritative so duplicate/stale queue messages
  cannot override cancellation/failure/dead-letter state.

Provider-switch relevance:

- Not required for provider switching.

## What Is Left In The Original Plan

## Remaining Stage 3 Work

### Step 3.3.3: Implement Local Secrets And Scoped Cache Store

Why the original plan includes it:

- Backend services need runtime secrets and scoped cache behavior that mimics
  cloud deployments.
- Secrets should not be hardcoded or pulled directly by arbitrary code.

Needed for provider switch?

- A secret provider is useful for API keys.
- Full local secrets/cache adapter work is not mandatory if switching provider
  in the current CLI and using environment variables.

### Step 3.3.4: Add Seed Commands

Why the original plan includes it:

- Local backend setup needs seed users, service identities, provider config,
  prompts, bucket, and maybe default roles.

Needed for provider switch?

- Not required unless provider config is persisted in DB.

## Remaining Stage 4: Auth And API Backend

Purpose:

Build the public backend API.

Remaining phases:

- API foundation and schemas
- auth, sessions, bearer tokens, CSRF, CORS, rate limits
- upload/run/artifact/event routes
- OpenAPI generation/review

Why needed:

- Required if users will interact with a hosted backend instead of the CLI.
- Required for multi-user uploads, run creation, artifact listing, and API
  security.

Needed for provider switch?

- No.

## Remaining Stage 5: Processor And Job Execution

Purpose:

Build the long-running worker that claims jobs and executes pipeline stages.

Remaining phases:

- processor command and service auth
- claim/heartbeat/lease/retry/cancel/dead-letter loop
- workspace materialization
- chunk/transcribe/reconcile/translate/article job execution
- retry idempotency
- temp object/workspace cleanup

Why needed:

- Required for asynchronous API-backed processing.
- Required if API and heavy model work run in separate services.

Needed for provider switch?

- No, unless provider calls are moving out of CLI into processor runtime now.

## Remaining Stage 6: Full Local Parity Stack

Purpose:

Make Docker Compose behave like the target backend stack.

Remaining phases:

- API and processor Dockerfiles
- finished Docker Compose services
- setup commands
- workflow docs
- compose fake-provider E2E test

Why needed:

- Required for local production-like testing and onboarding.

Needed for provider switch?

- No.

## Remaining Stage 7: Operational Hardening

Purpose:

Make the backend operable and safer under failure.

Remaining phases:

- structured logging and redaction
- cleanup scheduler
- outage/failure-mode tests
- backup/restore/operator docs

Why needed:

- Required before production operation.
- Protects secrets, user data, object storage, DB, queue, and provider spend.

Needed for provider switch?

- No.

## Remaining Stage 8: Cloudflare Adapter Spike

Purpose:

Evaluate Cloudflare-specific deployment options after the local provider-neutral
backend exists.

Remaining phases:

- re-verify Cloudflare limits
- prototype R2 object store adapter
- decide external PostgreSQL versus D1
- prototype Cloudflare Queue adapter if appropriate
- prototype thin Worker API/proxy if appropriate

Why needed:

- Cloudflare constraints change and must be verified close to deployment.
- The local ports make this spike lower risk.

Needed for provider switch?

- No.

## Remaining Stage 9: First Production Deployment

Purpose:

Deploy the chosen runtime topology.

Remaining phases:

- pick final runtime topology
- provision secrets/object storage/metadata DB/queue
- deploy API and processor with fake provider
- run one controlled real-provider test
- validate rollback and restore procedures

Why needed:

- Required to go live.

Needed for provider switch?

- No.

## If You Need To Switch AI Providers Now

Do not continue blindly through Phase 3+ if provider switching is the urgent
goal.

The likely minimal path is:

1. Identify the target provider(s):
   - transcription/ASR provider
   - text generation provider
   - embedding provider, if any, though this repo currently focuses on
     transcription and text generation

2. Implement provider adapters behind existing provider ports:
   - `AudioTranscriptionProvider`
   - `TextGenerationProvider`

3. Make the adapter consume existing request objects:
   - `AudioTranscriptionRequest`
   - `TextGenerationRequest`

4. Preserve prompt-safety rules:
   - developer/system instructions stay separate
   - user/source text stays in source data
   - source data must remain fenced/untrusted
   - provider output parsing remains schema-aware where possible

5. Add config/model-role mapping:
   - provider name
   - model IDs per role
   - API key env var / secret lookup

6. Add tests:
   - fake provider adapter test
   - request mapping test
   - prompt-injection fixture still passes
   - unit tests for all stages touched
   - safe CLI end-to-end test with fake provider

7. Only after provider switching works, decide whether to resume:
   - Step 3.3.3 if continuing backend infrastructure
   - Stage 4 if building API
   - Stage 5 if building processor

## Current High-Level Code Areas Added Or Changed

Architecture/planning:

- `planning/adr/0001-canonical-runtime-shape.md`
- `planning/adr/0002-processor-trust-boundary.md`
- `planning/adr/0003-opaque-object-keys.md`

Core boundaries:

- `src/urdu_pipeline/domain/*`
- `src/urdu_pipeline/application/ports/*`
- `src/urdu_pipeline/application/object_keys.py`

Infrastructure:

- `src/urdu_pipeline/infrastructure/in_memory.py`
- `src/urdu_pipeline/infrastructure/filesystem.py`
- `src/urdu_pipeline/infrastructure/db/*`
- `src/urdu_pipeline/infrastructure/s3.py`
- `src/urdu_pipeline/infrastructure/redis_queue.py`

Provider/stage safety:

- `src/urdu_pipeline/providers/requests.py`
- `src/urdu_pipeline/providers/base.py`
- `src/urdu_pipeline/providers/fake_provider.py`
- `src/urdu_pipeline/providers/openai_audio.py`
- `src/urdu_pipeline/providers/openai_text.py`
- `src/urdu_pipeline/stages/*`
- `src/urdu_pipeline/standalone/english_am_chunk_transcriber.py`

CLI/config/local stack:

- `src/urdu_pipeline/cli.py`
- `src/urdu_pipeline/config/settings.py`
- `pyproject.toml`
- `Makefile`
- `docker-compose.yml`
- `.env.example`
- `.env.local.example`

Tests:

- dependency boundary tests
- domain/state tests
- port tests
- in-memory adapter tests
- filesystem adapter tests
- provider request tests
- prompt-injection fixture tests
- stage tests
- migration tests
- PostgreSQL metadata tests
- S3 object-store tests
- Redis job-queue tests
- safe integration tests

## How To Hand This To Another AI Provider

Give the next AI these files first:

- This handoff:
  `planning/cloudflare/backend_api/cloud_agnostic_api_conversion_progress_handoff.md`
- Original plan:
  `planning/cloudflare/backend_api/cloud_agnostic_api_conversion_stepwise_commit_plan.md`
- Provider request models:
  `src/urdu_pipeline/providers/requests.py`
- Provider base interfaces:
  `src/urdu_pipeline/providers/base.py`
- Stage examples:
  `src/urdu_pipeline/stages/translator.py`
  `src/urdu_pipeline/stages/article_generator.py`
- Prompt-injection tests:
  `tests/unit/test_prompt_injection_fixtures.py`

Tell the next AI explicitly:

- If the goal is provider switching, do not continue the backend plan unless
  asked.
- Preserve all prompt-safety and provider-request boundaries.
- Run targeted tests before full suites.
- Do not revert unrelated work.

## Suggested Next Decision

Choose one of these tracks:

Track A: switch AI provider now

- Pause cloud/API plan.
- Implement provider adapter(s).
- Keep Stage 2 prompt-safety tests passing.
- Avoid spending time on DB/API/Redis/S3 unless needed by the provider switch.

Track B: continue backend conversion

- Continue original plan at Step 3.3.3.
- Implement local secrets and scoped cache store.
- Then seed commands, API, processor, and local parity stack.

Track C: stabilize and commit current work

- Review the current diff.
- Commit the completed Step 3.3.2 work if not already committed.
- Then decide Track A or Track B.
