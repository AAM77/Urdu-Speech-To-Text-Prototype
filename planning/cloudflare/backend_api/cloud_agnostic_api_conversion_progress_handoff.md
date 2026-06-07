# Cloud-Agnostic API Conversion Progress Handoff

Last updated: 2026-06-07 (session 15)

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
- AWS production adapter verification
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
  the plan from Step 4.1.2.

## Current Implementation Status

Completed through:

- Stage 0
- Stage 1
- Stage 2
- Stage 3 (all steps — 3.1.1 through 3.3.4 complete)
- Stage 4 (complete)
- Stage 5 (complete)
- Stage 6 (complete)
- Stage 7 Step 7.1.1 (complete)
- Stage 7 Step 7.1.2 (complete)
- Stage 7 Step 7.1.3 (complete)
- Stage 7 Step 7.2.1 (complete)
- Stage 8 Step 8.1.1 (complete)

Next step in the original plan:

- Stage 8: Step 8.1.2 — Verify PostgreSQL Metadata Store Against Managed RDS

Most recent verification:

- Combined unit + safe integration: `854 passed, 4 skipped`
- Full unit suite: `829 passed`
- `RUN_S3_OBJECT_STORE_SMOKE=1 make test-integration`: `25 passed, 4 skipped`
  (`test_s3_object_store_aws.py` skipped because `boto3` is not installed in
  the current environment; no AWS staging bucket/credentials were configured)
- Targeted Stage 8.1.1 S3/config/Makefile tests: `39 passed, 1 skipped`
- Initial Stage 8.1.1 red test run: `7 failed, 32 passed, 1 skipped`
- `git diff --check`: passed
- Step 7.2.1 docs-only verification: `git diff --check` passed
- Targeted Stage 7.1.3 failure-mode tests: `7 passed`
- Affected failure/lifecycle/artifact/provider/cleanup/metadata/migration
  tests: `100 passed`
- Targeted Stage 7.1.2 cleanup scheduler tests: `8 passed`
- Affected cleanup/metadata/auth/upload/dependency tests: `205 passed`
- Targeted Stage 7.1.1 redaction tests: `6 passed`
- Affected PostgreSQL/API/processor tests: `112 passed`
- Targeted compose fake-provider E2E contract tests: `10 passed`
- Affected compose/packaging/Makefile/migration/Postgres/API route tests:
  `216 passed`
- Targeted container packaging tests: `3 passed`
- Targeted Compose service tests: `5 passed`
- Targeted Compose + Makefile tests: `26 passed`
- Makefile local stack tests: `21 passed`
- Local workflow docs tests: `2 passed`
- Targeted docs + compose + Makefile tests: `28 passed`
- `docker compose --env-file .env.local.example config`: passed
- `make --no-print-directory -n compose-setup`: passed dry-run command review
- `make --no-print-directory -n compose-test`: passed dry-run command review
- `make --no-print-directory -n compose-fake-provider-e2e`: passed dry-run
  command review
- `docker compose --env-file .env.local.example --profile proxy config`: passed
- `git diff --check`: passed
- Docker build/start smoke attempted, but Docker daemon was not reachable at
  `unix:///Users/madeel/.colima/default/docker.sock`; image builds and service
  startup still need to be run once Colima/Docker is started.
- Real `make compose-test` attempted after Step 6.2.3, but Docker daemon was
  not reachable at `unix:///Users/madeel/.colima/default/docker.sock`.
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

### Step 3.3.3: Implement Local Secrets And Scoped Cache Store

What was done:

- Added `EnvSecretProvider` in `src/urdu_pipeline/infrastructure/secrets.py`.
  Reads from `os.environ`. Fails closed for both missing and empty-string values.
- Fixed `SecretValue.__repr__` and `__str__` in `application/ports/services.py` to
  show `<redacted>` instead of the raw value — prevents accidental secret leakage
  into logs or tracebacks.
- Added `test_postgres_metadata_store_cache_does_not_leak_across_users` to the
  postgres store test: verifies user A's cache entries are invisible to user B and
  that deleting A's entry does not touch B's.
- Exported `EnvSecretProvider` from `infrastructure/__init__.py`.

Why it was needed:

- The API and processor need to resolve secrets (API keys, DB credentials) from
  the environment without hardcoding them. `EnvSecretProvider` is the local
  implementation behind the `SecretProvider` port.
- `SecretValue` repr redaction is a safety rule — any logging of a `SecretValue`
  should never expose the actual value.

Provider-switch relevance:

- Directly relevant. Provider API keys (e.g. `OPENAI_API_KEY`) will be resolved
  through `EnvSecretProvider` in the processor rather than read from env directly.

### Step 3.3.4: Add Seed Commands

What was done:

- Added `src/urdu_pipeline/admin/__init__.py` and `src/urdu_pipeline/admin/seed.py`
  with four pure functions:
  - `seed_user(store, *, username)` → `UserRecord`
  - `seed_service_identity(store, *, name)` → `ServiceIdentityRecord`
  - `seed_provider_config(store, *, provider_name, model_roles)` → `ProviderConfigSnapshot`
  - `seed_bucket(client, *, bucket, region)` → `bool`
- Each function accepts the relevant store/client so they can be tested with
  in-memory fakes and wired to real adapters by CLI commands.
- Added four CLI commands to `cli.py`:
  - `urdu-pipeline seed-user --username <name>`
  - `urdu-pipeline seed-service-identity --name <name>`
  - `urdu-pipeline seed-provider-config [--provider-name <name>]`
  - `urdu-pipeline seed-bucket [--bucket <name>] [--endpoint-url <url>] [--region <r>]`
- Added object store settings to `Settings`:
  `OBJECT_STORE_ENDPOINT_URL`, `OBJECT_STORE_BUCKET`, `OBJECT_STORE_REGION`,
  `OBJECT_STORE_ACCESS_KEY`, `OBJECT_STORE_SECRET_KEY`
- Added `REDIS_URL` to `Settings`.
- Updated `.env.example` with new fields.

Why it was needed:

- Local backend setup must pre-populate the database with a user, a processor
  service identity, and a provider config before the API can serve real requests.
- `seed-bucket` idempotently creates the MinIO/S3 bucket without relying on
  auto-creation, using `head_bucket` + `create_bucket` with correct AWS region
  constraint rules.

Provider-switch relevance:

- `seed-provider-config` directly populates the DB with model roles from current
  settings. Any provider switch should re-run `seed-provider-config` to write a
  new versioned config snapshot.

## Stage 4 (In Progress): Auth And API Backend

### Step 4.1.1: Add FastAPI App Skeleton

What was done:

- Added `fastapi>=0.115,<1`, `uvicorn[standard]>=0.30,<1`, `httpx2>=2.0,<3` to
  `pyproject.toml` `api` and `dev` extras; installed in `.venv`.
- Added `src/urdu_pipeline/api/app.py` with `create_app(*, state)` factory.
  `AppState` is injected so tests use in-memory fakes and production wires real
  adapters without changing route code.
- Added `src/urdu_pipeline/api/dependencies.py` with `AppState` dataclass and
  `get_app_state`, `get_metadata_store`, `get_object_store`, `get_cache_store`,
  `get_secret_provider` FastAPI `Depends`-compatible functions.
- Added `src/urdu_pipeline/api/routes/__init__.py` and
  `src/urdu_pipeline/api/routes/health.py` with `GET /health` returning
  `{status: "ok", version: "0.1.0"}`.
- Health response never exposes internal config fields (tested explicitly).
- Unknown routes return 404 (tested).

Why it was needed:

- A FastAPI factory with injected adapters is the minimum testable API surface.
  The factory pattern ensures every future route test can use in-memory fakes.
- Keeping adapters injected (not imported as singletons) makes the app
  cloud-neutral: the same code runs locally with MinIO/Postgres and in production
  with S3/RDS without any route-level changes.

Provider-switch relevance:

- Not required for CLI provider switching.
- Required for the hosted API that stages 4-6 build.

### Step 4.1.2: Add Strict Public Request/Response Schemas

What was done:

- Added `src/urdu_pipeline/api/schemas.py` with 21 Pydantic v2 models in a
  `_StrictModel` base that sets `ConfigDict(extra="forbid")` on all schemas.
- Covered all six resource domains from the plan:
  - Auth: `LoginRequest`, `SessionResponse`
  - Tokens: `CreateTokenRequest`, `CreateTokenResponse`, `TokenSummary`,
    `TokenListResponse`, `RevokeTokenResponse`
  - Uploads: `InitUploadRequest`, `InitUploadResponse`, `UploadPartInfo`,
    `CompleteUploadRequest`, `UploadResponse`
  - Runs: `CreateRunRequest`, `RunResponse`, `RunListResponse`,
    `CancelRunResponse`
  - Events: `EventResponse`, `EventListResponse`
  - Artifacts: `ArtifactSummary`, `ArtifactListResponse`,
    `ArtifactDownloadResponse`
- Key security properties enforced:
  - `InitUploadResponse` and `ArtifactDownloadResponse` return signed URLs,
    never raw object keys.
  - `CreateRunRequest` accepts only `upload_id` and optional `description`.
    Provider, model, and prompt fields are server-controlled and not accepted.
  - `CreateTokenResponse` includes `token` (shown once); `TokenSummary` omits it.
  - `EventResponse` omits all pipeline text; callers use artifact download.

Why it was needed:

- Enforcing `extra="forbid"` at the schema layer means FastAPI returns 422 for
  any unknown field — no silent injection of provider/model/prompt fields.
- Keeping `user_id` and object keys out of all schemas prevents accidental
  leakage and enforces the ADR-003 opaque-keys constraint.

Provider-switch relevance:

- Not required for CLI provider switching.
- Required for the API that accepts run creation from external callers.

### Step 4.2.1: Add Admin CLI For Users And Service Identities

What was done:

- Added `password_hash: str | None = None` to `UserRecord` (backward-compatible).
- Added `update_user`, `list_users`, `update_service_identity` to the `MetadataStore`
  Protocol and to `InMemoryMetadataStore`.
- Created `src/urdu_pipeline/admin/users.py` with five pure admin functions behind
  narrow protocols (`_UserAdminStore`, `_ServiceIdentityAdminStore`, `_PasswordHasher`):
  - `admin_create_user(store, hasher, *, username, password)` → `UserRecord`
  - `admin_reset_password(store, hasher, *, user_id, new_password)` → `UserRecord`
  - `admin_disable_user(store, *, user_id)` → `UserRecord`
  - `admin_list_users(store)` → `list[UserRecord]`
  - `admin_revoke_service_identity(store, *, service_identity_id)` → `ServiceIdentityRecord`
- Added five CLI commands to `cli.py`:
  - `urdu-pipeline admin-create-user --username <name> --password <pw>`
  - `urdu-pipeline admin-reset-password --user-id <id> --new-password <pw>`
  - `urdu-pipeline admin-disable-user --user-id <id>`
  - `urdu-pipeline admin-list-users`
  - `urdu-pipeline admin-revoke-service-identity --service-identity-id <id>`
- Added `_Pbkdf2Hasher` placeholder in `cli.py` (PBKDF2-HMAC-SHA256 with random salt),
  later replaced with `BcryptHasher` delegation in Step 4.2.2.
- No public signup endpoint; these commands are operator-only.

Why it was needed:

- There is no public signup; all users must be pre-configured by an operator.
- Resetting passwords, disabling users, and revoking service identities are essential
  lifecycle operations before the API is exposed.
- Narrow protocols keep the functions testable without a real database.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 4.2.2: Login, Logout, Session Resolution

What was done:

- Added `bcrypt>=4.0,<5` to `api` and `dev` extras in `pyproject.toml`.
- Added `SessionId(prefix="ses")` to `domain/ids.py` and re-exported from `domain/__init__.py`.
- Added `SessionRecord` dataclass to `application/ports/services.py`:
  - Fields: `session_id`, `user_id`, `token_hash` (SHA-256 hex), `expires_at`, `created_at`, `revoked_at`.
  - Token hash uses SHA-256 (not bcrypt) because session tokens are already high-entropy random values;
    bcrypt would be unnecessary overhead. Bcrypt is reserved for low-entropy passwords.
- Extended `MetadataStore` Protocol with:
  - `get_user_by_username(username: str) -> UserRecord | None`
  - `create_session(record: SessionRecord) -> None`
  - `get_session_by_token_hash(token_hash: str) -> SessionRecord | None`
  - `revoke_session(session_id: SessionId, *, revoked_at: datetime) -> None`
- Implemented all new `MetadataStore` methods in `InMemoryMetadataStore`:
  - Sessions stored in two dicts: by `session_id` and by `token_hash` for O(1) lookup.
  - `revoke_session` uses `dataclasses.replace` to create updated record with `revoked_at` set.
- Created `src/urdu_pipeline/auth/__init__.py` (package marker).
- Created `src/urdu_pipeline/auth/hashing.py`:
  - `PasswordHasher` Protocol with `hash_secret` and `verify_secret`.
  - `BcryptHasher` concrete implementation (rounds=12 default).
  - Lazy imports of `bcrypt` to avoid import-time cost when not used.
- Created `src/urdu_pipeline/auth/sessions.py`:
  - `_hash_token(raw_token)` → SHA-256 hex digest (internal).
  - `create_session(store, *, user_id, expires_in=7d) -> (raw_token, SessionRecord)`.
  - `resolve_session(store, *, raw_token) -> AuthPrincipal | None`.
  - `revoke_session(store, *, session_id) -> None`.
  - All three use a narrow `_SessionStore` protocol for testability.
- Updated `AppState` in `api/dependencies.py`:
  - Added `password_hasher: PasswordHasher = field(default_factory=BcryptHasher)`.
  - Default means existing tests that create `AppState` still work without specifying a hasher.
  - Added `get_password_hasher` dependency function.
- Created `src/urdu_pipeline/api/routes/auth.py`:
  - `POST /auth/login` — verifies username/password, creates session, sets HTTP-only cookie.
  - `POST /auth/logout` — reads cookie, revokes session if valid, clears cookie.
  - Cookie name: `"session"`. `httponly=True`, `samesite="lax"`, `secure=False` (dev; will be True in prod).
  - Session lifetime: 7 days.
  - Login returns `SessionResponse` (only `username`; never exposes `user_id` or internal IDs).
  - Login errors for unknown user, wrong password, disabled user all return identical 401 to prevent enumeration.
- Updated `api/app.py` to include `auth_router`.
- Updated `cli.py`: replaced `_Pbkdf2Hasher` PBKDF2 implementation with delegation to `BcryptHasher`.
- Wrote `tests/unit/test_auth_sessions.py` (17 tests):
  - Session creation returns raw token + record with SHA-256 hash stored.
  - Resolve returns `AuthPrincipal` for valid sessions; `None` for expired/revoked/unknown.
  - Revoke stores `revoked_at` timestamp.
- Wrote `tests/unit/test_auth_routes.py` (11 tests):
  - Login: 200 on success, body contains `username` not `user_id`, cookie is HTTP-only with samesite attribute.
  - Login: 401 for wrong password, unknown user, disabled user.
  - Login: 422 for extra (injected) fields — schema strictly forbids unknown fields.
  - Logout: 200 always, session revoked in store, cookie cleared.
- Test count: 398 passed, 3 skipped.

Why it was needed:

- Session auth is the primary mechanism for browser-based clients.
- HTTP-only cookies prevent XSS from stealing tokens.
- SHA-256 session token hashing means a DB breach does not reveal live session tokens.
- The `BcryptHasher` replaces the PBKDF2 placeholder so passwords are consistently hashed with
  the same algorithm used for verification in the login route.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 4.2.3: Add Bearer Token Auth

What was done (strict TDD — tests written and confirmed failing before implementation):

- Added `TokenId(prefix="tok")` to `domain/ids.py` and `domain/__init__.py`.
- Added `BearerTokenRecord` dataclass to `application/ports/services.py`:
  - Fields: `token_id`, `user_id`, `token_hash` (SHA-256), `name`, `description`,
    `created_at`, `expires_at`, `revoked_at`, `last_used_at`.
  - Same SHA-256-for-hash approach as session tokens: high-entropy random values
    do not need bcrypt.
  - `last_used_at` is `None` at creation; updated on every successful resolution.
- Extended `MetadataStore` Protocol with:
  - `create_bearer_token(record: BearerTokenRecord) -> None`
  - `get_bearer_token_by_hash(token_hash: str) -> BearerTokenRecord | None`
  - `get_bearer_token(token_id: TokenId) -> BearerTokenRecord | None`
  - `update_bearer_token(record: BearerTokenRecord) -> None`
  - `list_bearer_tokens_for_user(user_id: UserId) -> Sequence[BearerTokenRecord]`
- Implemented all five new methods in `InMemoryMetadataStore`:
  - Dual-index: `_bearer_tokens` (by `TokenId`) and `_bearer_tokens_by_hash` (by SHA-256).
  - `update_bearer_token` keeps both indices in sync.
- Created `src/urdu_pipeline/auth/bearer.py`:
  - `create_bearer_token(store, *, user_id, name, description, expires_in) -> (raw_token, record)`
  - `resolve_bearer_token(store, *, raw_token) -> AuthPrincipal | None`
    — updates `last_used_at` in the store on every successful resolution.
  - `revoke_bearer_token(store, *, token_id) -> None`
    — raises `KeyError` for unknown `token_id` (surfaces as 404 in routes).
  - Uses narrow `_BearerStore` and `_BearerRevokeStore` protocols for testability.
- Updated `api/dependencies.py`:
  - Added `get_principal_from_session` — resolves session cookie to `AuthPrincipal | None`.
  - Added `get_principal_from_bearer` — resolves `Authorization: Bearer` header to `AuthPrincipal | None`.
  - Added `require_principal` — accepts either session OR bearer; raises 401 if neither.
  - Added `require_session_principal` — session cookie only; used on token management routes
    so bearer tokens cannot mint more bearer tokens (prevents proliferation).
- Created `src/urdu_pipeline/api/routes/tokens.py`:
  - `POST /tokens` — create (requires session auth), returns raw token once.
  - `GET /tokens` — list (accepts session OR bearer), never includes raw token or hash.
  - `DELETE /tokens/{token_id}` — revoke (requires session auth), returns 404 for unknown/other-user tokens.
- Included `tokens_router` in `api/app.py`.
- Wrote `tests/unit/test_bearer_tokens.py` (22 tests) BEFORE implementation:
  - Creation: raw token returned, SHA-256 hash stored, record retrievable, no revocation/last_used_at on creation.
  - Resolution: valid token → `AuthPrincipal`; expired/revoked/unknown → None; `last_used_at` updated.
  - Revocation: `revoked_at` stored; revoked token resolves to None; missing `token_id` raises `KeyError`.
- Wrote `tests/unit/test_token_routes.py` (26 tests) BEFORE implementation:
  - `POST /tokens`: 401 without auth, 200 with session, raw token in response, 422 on extra fields.
  - `GET /tokens`: 401 without auth, 200 with session, raw token never in list, hash never in list.
  - `DELETE /tokens/{id}`: 401 without auth, 200 on success, 404 for unknown token.
  - Bearer auth: valid token authenticates GET /tokens; invalid/expired/revoked returns 401.
- Test count: 446 passed, 3 skipped.

Why it was needed:

- Bearer tokens allow programmatic API access without a browser session (CI/CD, scripts).
- Tokens are shown once so a DB breach cannot reveal live token values.
- `last_used_at` gives operators audit visibility without exposing raw credentials.
- `require_session_principal` on write routes prevents bearer tokens from being used to
  create/revoke more tokens (no unbounded self-replication).

Provider-switch relevance:

- Not required for CLI provider switching.

## Stage 6 (In Progress): Full Local Parity Stack

Purpose:

Make local development mimic the API + processor + Postgres + object-store +
queue topology closely enough that pre-deployment tests exercise production-like
runtime boundaries.

### Phase 6.1: Containers And Compose

### Step 6.1.1: Add API And Processor Dockerfiles

What was done (strict TDD — tests written and confirmed failing before implementation):

- Added `tests/unit/test_container_packaging.py` before implementation.
- Confirmed the new tests failed for the expected reason:
  missing `Dockerfile.api`, `Dockerfile.processor`, and `.dockerignore`.
- Added `Dockerfile.api`:
  - Uses `python:3.12-slim`.
  - Installs the project with the `api` extra.
  - Starts `uvicorn urdu_pipeline.api.app:create_app --factory` on
    `0.0.0.0:8000`.
  - Exposes port `8000`.
- Added `Dockerfile.processor`:
  - Uses `python:3.12-slim`.
  - Installs Debian `ffmpeg`, and verifies both `ffmpeg -version` and
    `ffprobe -version` during build.
  - Installs the project with `processor` and `cli` extras so the existing
    `urdu-pipeline process` command can run.
  - Defaults `PROCESSOR_API_URL=http://api:8000` and starts the processor
    command with that URL.
- Added `.dockerignore` to keep local state, virtualenvs, caches, run outputs,
  input audio, git metadata, and env files out of the Docker build context.

Verification:

- Red test run before implementation:
  `.venv/bin/python -m pytest tests/unit/test_container_packaging.py -q`
  failed with 3 expected missing-file failures.
- Targeted packaging test after implementation:
  `.venv/bin/python -m pytest tests/unit/test_container_packaging.py -q`
  passed (`3 passed`).
- Targeted API/processor dependency/auth tests:
  `.venv/bin/python -m pytest tests/unit/test_dependency_boundaries.py tests/unit/test_service_auth.py tests/unit/test_api_skeleton.py -q`
  passed (`32 passed`).
- Full local unit + safe integration suite:
  `.venv/bin/python -m pytest tests/unit tests/integration_safe -q`
  passed (`799 passed, 3 skipped`).
- `git diff --check` passed.
- Docker build smoke was attempted:
  `docker build -f Dockerfile.api -t urdu-pipeline-api:step-6.1.1 .`
  could not run because the Docker daemon was not reachable at
  `unix:///Users/madeel/.colima/default/docker.sock`.

Why it was needed:

- The API and processor now have separate container build artifacts, matching
  the runtime split established by the ADRs.
- The API image installs only the API runtime surface rather than UI or
  processor-only dependencies.
- The processor image includes `ffmpeg`/`ffprobe`, which are required for audio
  validation and chunking inside the background processor.
- `.dockerignore` prevents secrets and large local artifacts from being sent to
  Docker builds.

Needed for provider switch only?

- No. This is local parity/backend deployment infrastructure.

### Step 6.1.2: Finish Docker Compose Services

What was done (strict TDD — tests written and confirmed failing before implementation):

- Added `tests/unit/test_compose_services.py` before implementation.
- The first red run failed for expected reasons:
  - `api` had no `build` context and was still using `python:3.12-slim` with
    `python -m http.server`.
  - `processor` had no `build` context and was still using a Python sleep
    placeholder.
  - `reverse-proxy` had no mounted Nginx config.
- Added an additional failing assertion for image-internal paths after Compose
  rendered `/workspace/...` from `.env.local.example`; this caught a real
  mismatch because the API/processor services no longer bind-mount the repo.
- Updated `docker-compose.yml`:
  - `api` now builds from `Dockerfile.api`, uses image
    `urdu-pipeline-api:local`, runs `uvicorn
    urdu_pipeline.api.app:create_app --factory`, exposes port `8000`, and
    health-checks `GET /health`.
  - `processor` now builds from `Dockerfile.processor`, uses image
    `urdu-pipeline-processor:local`, waits for the API plus PostgreSQL, MinIO,
    and Redis health checks, validates `urdu-pipeline process --dry-run`, writes
    `/tmp/processor-ready`, and health-checks both readiness and `ffprobe`.
  - Both API and processor use container-internal `/app` paths and no longer
    bind-mount the repository.
  - API/processor environment now includes `DATABASE_URL`, `REDIS_URL`,
    `SERVICE_AUTH_TOKEN`, S3/MinIO endpoint URL, bucket/region, object-store
    access keys, and AWS-compatible credential env vars for boto3.
  - PostgreSQL, MinIO, and Redis retain persistent named volumes and health
    checks.
  - Optional `reverse-proxy` profile mounts a concrete Nginx config and proxies
    to the API service.
- Updated `.env.local.example`:
  - Added `PROCESSOR_API_URL`, `SERVICE_AUTH_TOKEN`, object-store endpoint URL,
    object-store credentials, and AWS-compatible MinIO credentials.
  - Changed local container paths from `/workspace/...` to `/app/...`.
- Added `deploy/nginx/default.conf` to proxy all requests to `http://api:8000`
  while forwarding common proxy headers.

Important limitation at the time of Step 6.1.2:

- The processor service ran `urdu-pipeline process --dry-run`, marked
  itself ready, and stays alive. This is explicit rather than hidden: the
  existing CLI `process` command validates service auth but still exits because
  the long-running command-shell loop has not yet been wired into `cli.py`.
  The pure lifecycle and stage functions exist from Stage 5, but the CLI shell
  still needs future integration before a real compose E2E can process jobs.
- Resolved by Step 6.2.3: the compose processor now runs the real polling loop
  and `make compose-test` is wired to the fake-provider E2E smoke.

Verification:

- Red test run before implementation:
  `.venv/bin/python -m pytest tests/unit/test_compose_services.py -q`
  failed with expected compose-contract failures.
- Targeted compose test after implementation:
  `.venv/bin/python -m pytest tests/unit/test_compose_services.py -q`
  passed (`5 passed`).
- Targeted packaging + compose + Makefile tests:
  `.venv/bin/python -m pytest tests/unit/test_container_packaging.py tests/unit/test_compose_services.py tests/integration_safe/test_makefile.py -q`
  passed (`20 passed`).
- Compose config validation:
  `docker compose --env-file .env.local.example --profile proxy config`
  passed and rendered API, processor, PostgreSQL, MinIO, Redis, and
  reverse-proxy services.
- Full local unit + safe integration suite:
  `.venv/bin/python -m pytest tests/unit tests/integration_safe -q`
  passed (`804 passed, 3 skipped`).
- `git diff --check` passed.
- Service startup probe:
  `docker compose --env-file .env.local.example up --no-start`
  could not run because the Docker daemon was not reachable at
  `unix:///Users/madeel/.colima/default/docker.sock`.

Why it was needed:

- Compose now mirrors the intended service topology: separately built API and
  processor containers plus PostgreSQL, MinIO/S3-compatible object storage, and
  Redis/Valkey.
- Health-gated dependencies make startup ordering explicit and closer to the
  target deployment topology.
- Removing repo bind mounts means the compose stack now exercises the packaged
  images rather than the host checkout.
- The proxy profile is concrete enough to validate locally without making it a
  mandatory part of the default development stack.

Needed for provider switch only?

- No. This is local parity/backend deployment infrastructure.

### Phase 6.2: Local Commands, Docs, And E2E

### Step 6.2.1: Add Local Setup Commands

What was done (strict TDD — tests written and confirmed failing before implementation):

- Updated `tests/integration_safe/test_makefile.py` before implementation.
- The red run failed for expected reasons:
  - Missing `api-dev`, `processor-dev`, `compose-down`, `compose-setup`, and
    individual compose setup targets.
  - Existing `compose-up` and `compose-test` still printed "not implemented yet".
- Replaced placeholder Makefile targets with concrete local-stack commands.
- Added runtime targets:
  - `make api-dev` — runs `uvicorn
    urdu_pipeline.api.app:create_app --factory` with configurable
    `API_HOST`/`API_PORT`.
  - `make processor-dev` — runs `urdu-pipeline process` through the local CLI
    with configurable `SERVICE_AUTH_TOKEN` and `PROCESSOR_API_URL`.
  - `make compose-up` — runs `docker compose --env-file .env.local.example up
    --build -d --wait`.
  - `make compose-down` — runs `docker compose --env-file .env.local.example
    down`.
  - `make compose-test` — validates compose config, starts the stack with
    `--wait`, prints service status, checks API `/health`, and checks the
    processor readiness marker.
- Added setup targets:
  - `make compose-migrate` — runs metadata migrations against local published
    PostgreSQL via `LOCAL_DATABASE_URL`.
  - `make compose-seed-bucket` — ensures the local MinIO bucket exists via the
    host CLI and `LOCAL_OBJECT_STORE_ENDPOINT_URL`.
  - `make compose-seed-user` — creates a local login user with
    `admin-create-user` and configurable `LOCAL_USERNAME`/`LOCAL_PASSWORD`.
  - `make compose-seed-service-identity` — seeds a processor service identity.
  - `make compose-seed-provider-config` — seeds the fake-provider config by
    default.
  - `make compose-setup` — runs compose-up, migrations, bucket setup, user
    seed, service identity seed, and provider config seed in order.
- Updated Makefile help output and `.PHONY` declarations for all new targets.

Implementation note:

- The setup targets intentionally run through the local venv CLI against
  published localhost ports instead of `docker compose run processor`.
  The processor image is a processor runtime, while the local operator machine
  already has the dev extra set available for admin/password hashing commands.

Verification:

- Red Makefile test run before implementation:
  `.venv/bin/python -m pytest tests/integration_safe/test_makefile.py -q`
  failed with 11 expected target/placeholder failures.
- Targeted Makefile tests after implementation:
  `.venv/bin/python -m pytest tests/integration_safe/test_makefile.py -q`
  passed (`21 passed`).
- Targeted Compose + Makefile tests:
  `.venv/bin/python -m pytest tests/unit/test_compose_services.py tests/integration_safe/test_makefile.py -q`
  passed (`26 passed`).
- Dry-run command review:
  `make --no-print-directory -n compose-setup` passed and printed setup targets
  in the expected order.
- Dry-run compose smoke review:
  `make --no-print-directory -n compose-test` passed and printed config/start,
  `ps`, API health, and processor-ready checks.
- Compose config validation:
  `docker compose --env-file .env.local.example --profile proxy config` passed.
- Full local unit + safe integration suite:
  `.venv/bin/python -m pytest tests/unit tests/integration_safe -q`
  passed (`813 passed, 3 skipped`).
- `git diff --check` passed.
- Real compose smoke:
  `make --no-print-directory compose-test` could not run because the Docker
  daemon was not reachable at
  `unix:///Users/madeel/.colima/default/docker.sock`.

Why it was needed:

- Developers now have concrete commands for running API and processor surfaces
  locally and for bringing up/down the Compose parity stack.
- Local setup is decomposed into reviewable, repeatable commands for database
  migrations, user creation, service identity creation, provider config seeding,
  and bucket creation.
- `compose-test` provides a smoke target for the stack without implementing the
  full fake-provider E2E workflow reserved for Step 6.2.3.

Needed for provider switch only?

- No. This is local parity/backend workflow infrastructure.

### Step 6.2.2: Add Local Workflow Documentation

What was done (TDD-style documentation validation):

- Added `tests/unit/test_local_workflow_docs.py` before writing the document.
- The red run failed for expected reasons:
  - `docs/local_api_workflow.md` did not exist.
  - `README.md` did not link to the local API workflow doc.
- Added `docs/local_api_workflow.md` covering:
  - fake-provider mode as the local default;
  - `make compose-setup`, `make compose-test`, and `make compose-down`;
  - `SERVICE_AUTH_TOKEN` and local-only secret guidance;
  - session login with `POST /auth/login`;
  - CSRF cookie/header handling with `csrf_token`;
  - bearer token creation with `POST /tokens`;
  - direct upload with `POST /uploads/direct`;
  - signed upload with `POST /uploads/init` plus completion;
  - run creation with `POST /runs`;
  - polling with `GET /runs/{run_id}` and `GET /runs/{run_id}/events`;
  - artifact listing, metadata, and signed downloads via
    `GET /runs/{run_id}/artifacts`, `GET /artifacts/{artifact_id}`, and
    `GET /artifacts/{artifact_id}/download`;
  - cancellation with `POST /runs/{run_id}/cancel`;
  - retry and cleanup behavior from the processor lifecycle/idempotency/cleanup
    modules;
  - object-key non-disclosure and opaque server-side object key derivation;
  - then-current local limitations before Step 6.2.3: API container lacked
    env-built production `AppState`, processor command shell was still dry-run,
    and full compose E2E remained pending.
- Updated `README.md` with a short "Local API-backed workflow" section that
  links to `docs/local_api_workflow.md`.

Verification:

- Red documentation test before implementation:
  `.venv/bin/python -m pytest tests/unit/test_local_workflow_docs.py -q`
  failed with 2 expected missing-doc/link failures.
- Targeted documentation test after implementation:
  `.venv/bin/python -m pytest tests/unit/test_local_workflow_docs.py -q`
  passed (`2 passed`).
- Targeted docs + compose + Makefile tests:
  `.venv/bin/python -m pytest tests/unit/test_local_workflow_docs.py tests/unit/test_compose_services.py tests/integration_safe/test_makefile.py -q`
  passed (`28 passed`).
- Full local unit + safe integration suite:
  `.venv/bin/python -m pytest tests/unit tests/integration_safe -q`
  passed (`815 passed, 3 skipped`).
- `git diff --check` passed.

Why it was needed:

- The local stack now has a single operator/client workflow reference for
  setup, auth, uploads, run lifecycle, artifact retrieval, cancellation, retry,
  and cleanup behavior.
- The doc explicitly distinguishes implemented route contracts from current
  runtime limitations, preventing false confidence before Step 6.2.3.

Needed for provider switch only?

- No. This is local API/backend workflow documentation.

### Step 6.2.3: Add Compose Fake-Provider E2E Test

What was done (strict TDD):

- Added failing tests before implementation:
  - `tests/unit/test_runtime_app_state.py` for environment-backed API
    `AppState` wiring.
  - `tests/unit/test_durable_artifact_repository.py` for durable artifact JSON,
    Markdown, metadata, and document chunk persistence.
  - `tests/unit/test_processor_cli_runtime.py` for real processor CLI loop
    invocation and bounded `--once` behavior.
  - `tests/unit/test_compose_fake_provider_e2e.py` for compose wiring that runs
    the real processor loop and invokes the fake-provider E2E smoke module.
  - Expanded migration/Postgres metadata tests for password hashes, sessions,
    upload/run/job updates, artifact markdown metadata, and job completion.
- The red targeted run failed for expected missing pieces: no runtime API
  factory, no durable artifact repository, no processor loop hook, compose still
  used `--dry-run`, no migration `0004`, and Postgres user records did not
  round-trip `password_hash`.
- Added `src/urdu_pipeline/api/runtime.py`:
  - Builds production/local `AppState` from environment-backed settings.
  - Wires `PostgresMetadataStore`, `S3ObjectStore`, `RedisJobQueue`,
    `EnvSecretProvider`, and `SERVICE_AUTH_TOKEN`.
- Added `src/urdu_pipeline/infrastructure/artifacts.py`:
  - Persists artifact JSON and Markdown into object storage under
    route-compatible opaque keys.
  - Records safe artifact metadata and document chunks.
- Added `src/urdu_pipeline/processor/runtime.py`:
  - Runs a polling processor loop.
  - Pings `/internal/ping` with `SERVICE_AUTH_TOKEN`.
  - Claims jobs through Redis/Postgres.
  - Materializes uploaded audio from object storage.
  - Runs chunker, fake-provider transcriber, reconciler, translator, and article
    stages.
  - Persists JSON/Markdown artifacts, stage events, document chunks, and cleans
    temp run object prefixes.
- Updated `urdu-pipeline process`:
  - Keeps `--dry-run` as configuration validation.
  - Adds `--once` for bounded processor runs/tests.
  - Calls the real `run_processor(...)` loop otherwise.
- Added `src/urdu_pipeline/tools/compose_fake_provider_e2e.py`:
  - Runs inside the API container.
  - Seeds a real browser-like session with CSRF.
  - Uploads a generated WAV through `POST /uploads/direct`.
  - Creates a run, polls to `succeeded`, checks persisted events, checks JSON
    and Markdown artifact downloads, checks DB document chunks, checks object
    store outputs, and verifies temp run object cleanup.
- Added migration
  `src/urdu_pipeline/infrastructure/db/migration_files/0004_add_runtime_adapter_fields.sql`
  for runtime fields:
  - `users.password_hash`
  - `runs.description`
  - `uploads.multipart_upload_id`
  - `api_tokens.name`
  - `api_tokens.description`
- Expanded `PostgresMetadataStore` for runtime API/processor needs:
  - username lookup, user updates, list users;
  - service identity lookup/update;
  - sessions and user bearer token persistence including token
    name/description;
  - upload/run/job updates and processor job completion;
  - run artifact listing and artifact markdown metadata through artifact
    manifest JSON.
- Made local setup seeding repeatable where needed:
  - `admin-create-user` refreshes an existing username's password hash/status.
  - `seed-service-identity` returns an existing service identity by name.
- Updated `Dockerfile.api` and `docker-compose.yml`:
  - API now starts `urdu_pipeline.api.runtime:create_runtime_app`.
  - Processor now runs the real processor command instead of `--dry-run`.
- Updated `Makefile`:
  - `compose-test` now starts the stack, runs migrations/seeding/bucket setup,
    checks API health, and runs the fake-provider E2E smoke module.
  - Added `compose-fake-provider-e2e`.
- Updated `GET /runs/{run_id}/events` to return persisted stage events for
  stores that implement `list_stage_events`, while preserving empty responses
  for simpler test stores.
- Updated `docs/local_api_workflow.md` to remove stale limitations and describe
  the completed compose fake-provider E2E path.

Verification:

- Red targeted test before implementation:
  `.venv/bin/python -m pytest tests/unit/test_runtime_app_state.py tests/unit/test_durable_artifact_repository.py tests/unit/test_processor_cli_runtime.py tests/unit/test_compose_fake_provider_e2e.py tests/unit/test_postgres_metadata_store.py::test_postgres_metadata_store_supports_runtime_auth_and_lifecycle_updates tests/unit/test_migrations.py::test_load_migrations_includes_runtime_adapter_fields -q`
  failed with expected missing runtime/artifact/processor/compose/migration/Postgres
  contract failures.
- Targeted green test:
  `.venv/bin/python -m pytest tests/unit/test_runtime_app_state.py tests/unit/test_durable_artifact_repository.py tests/unit/test_processor_cli_runtime.py tests/unit/test_compose_fake_provider_e2e.py tests/unit/test_postgres_metadata_store.py::test_postgres_metadata_store_supports_runtime_auth_and_lifecycle_updates tests/unit/test_migrations.py::test_load_migrations_includes_runtime_adapter_fields -q`
  passed (`10 passed`).
- Affected broad subset:
  `.venv/bin/python -m pytest tests/unit/test_compose_services.py tests/unit/test_container_packaging.py tests/integration_safe/test_makefile.py tests/unit/test_migrations.py tests/unit/test_postgres_metadata_store.py tests/unit/test_auth_routes.py tests/unit/test_token_routes.py tests/unit/test_run_routes.py tests/unit/test_artifact_routes.py tests/unit/test_upload_routes.py tests/unit/test_service_auth.py -q`
  passed (`216 passed`).
- Full local unit + safe integration suite:
  `.venv/bin/python -m pytest tests/unit tests/integration_safe -q`
  passed (`825 passed, 3 skipped`).
- Compose config:
  `docker compose --env-file .env.local.example config` passed.
- Compose proxy config:
  `docker compose --env-file .env.local.example --profile proxy config` passed.
- Makefile dry-runs:
  `make --no-print-directory -n compose-test` passed.
  `make --no-print-directory -n compose-fake-provider-e2e` passed.
- `git diff --check` passed.
- Real compose E2E attempt:
  `make --no-print-directory compose-test` failed because Docker/Colima daemon
  was not reachable at
  `unix:///Users/madeel/.colima/default/docker.sock`.

Why it was needed:

- Local Compose now exercises the real API/processor/database/object-store/queue
  topology instead of only checking health/readiness placeholders.
- The fake-provider E2E validates the user-facing API path through login, CSRF,
  upload, run creation, processor completion, events, artifacts, object store
  output, DB document chunks, no private response fields, and temp cleanup.

Needed for provider switch only?

- No. This is backend API/processor local parity infrastructure.

## Later Completed Steps And Remaining Plan Status

### Step 4.2.4: Add CSRF, CORS, And Rate Limits

What was done (strict TDD — tests written and confirmed failing before implementation):

- Created `src/urdu_pipeline/api/middleware/__init__.py` (package marker).
- Created `src/urdu_pipeline/api/middleware/csrf.py`:
  - `generate_csrf_token()` — generates a `secrets.token_hex(16)` CSRF nonce.
  - `require_csrf` FastAPI dependency — double-submit cookie pattern:
    - Reads `session` cookie, `csrf_token` cookie, and `X-CSRF-Token` header.
    - If no session cookie: skips CSRF check (auth dependency raises 401 instead).
    - If session present but no/wrong CSRF header: raises HTTP 403.
    - Uses `secrets.compare_digest` to prevent timing attacks.
- Created `src/urdu_pipeline/api/middleware/rate_limit.py`:
  - `RateLimiter` Protocol — `check_and_increment(key: str) -> bool`.
  - `InMemoryRateLimiter` — fixed-window counter using `time.monotonic()`.
    Configurable `limit` and `window_seconds`. Thread-safety note documented.
- Updated `AppState` in `api/dependencies.py`:
  - Added `login_rate_limiter: RateLimiter = InMemoryRateLimiter(limit=10, window_seconds=60)`.
  - Added `get_login_rate_limiter` dependency.
  - Added `check_login_rate_limit` dependency — keys on `login:{client_ip}`, raises 429.
- Updated `api/routes/auth.py`:
  - `POST /auth/login` now has `check_login_rate_limit` as a route dependency.
  - Login response sets a second cookie: `csrf_token` (NOT httponly, samesite=strict).
    Client JS reads this and includes it as `X-CSRF-Token` on mutating requests.
- Updated `api/routes/tokens.py`:
  - `POST /tokens` and `DELETE /tokens/{id}` now have `require_csrf` as a route dependency.
  - `GET /tokens` is idempotent — no CSRF required.
- Updated `api/app.py` (`create_app`):
  - Added `allowed_origins: list[str] | None` parameter.
  - Adds `CORSMiddleware` when `allowed_origins` is non-empty.
  - `["*"]` allows all origins; `[]` (default) disables cross-origin headers.
- Updated `tests/unit/test_token_routes.py`:
  - `_logged_in_client()` now returns `(client, store, csrf_token)` (3-tuple).
  - All mutating calls in the test file updated to include `headers={"X-CSRF-Token": csrf}`.
- Wrote `tests/unit/test_security_middleware.py` (22 tests) BEFORE implementation:
  - CSRF: login sets a non-httponly `csrf_token` cookie; POST/DELETE without the
    header get 403; with matching header they succeed; wrong value gets 403.
  - CSRF ordering: unauthenticated POST → 401 (auth fires), not 403 (CSRF fires).
  - CORS: allowed origin gets `Access-Control-Allow-Origin`; disallowed does not;
    OPTIONS preflight succeeds; empty allowlist = no CORS headers; `*` allows all.
  - Rate limiting: requests within limit succeed; requests over limit get 429;
    boundary request still allowed; health endpoint unaffected by login limiter.
- Test count: 468 passed, 3 skipped.

Why it was needed:

- CSRF: session cookies are sent by browsers automatically — without CSRF protection
  a malicious page could trigger state mutations on behalf of a logged-in user.
- Double-submit pattern is stateless (no server-side CSRF token store needed).
- Bearer token requests are inherently CSRF-safe (browsers never auto-attach them).
- CORS allowlist prevents other web origins from reading API responses.
- Login rate limiting mitigates brute-force password attacks.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 4.3.1: Add Upload Init And Complete Routes

What was done (strict TDD — tests written and confirmed failing before implementation):

- Extended `UploadRecord` in `src/urdu_pipeline/application/ports/services.py`:
  - Added `content_type: str | None = None`.
  - Added `size_bytes: int | None = None`.
  These fields are stored internally but never returned alongside the internal object key.
- Added `update_upload(record: UploadRecord) -> None` to the `MetadataStore` protocol.
- Implemented `update_upload` in `InMemoryMetadataStore` (raises `KeyError` for unknown IDs).
- Updated `InitUploadRequest` in `src/urdu_pipeline/api/schemas.py`:
  - Added Pydantic `field_validator` validators for `filename` (non-empty, allowed extension),
    `content_type` (must be in allowed set), and `size_bytes` (> 0, ≤ 500 MB).
  - Allowed extensions: `.aac`, `.flac`, `.m4a`, `.mp3`, `.mp4`, `.ogg`, `.opus`, `.wav`, `.webm`.
  - Allowed content-types: all common audio/video MIME types for those formats.
  - `extra="forbid"` inherited from `_StrictModel` — unknown fields yield 422.
- Created `src/urdu_pipeline/api/routes/uploads.py`:
  - `POST /uploads/init` — requires `require_principal` (session or bearer) + `require_csrf`.
    1. Derives internal object key as `uploads/{upload_id}` — never exposed in response.
    2. Calls `ObjectStore.create_signed_upload_url(key, expires_in=1h)`.
    3. Creates `UploadRecord(status=INITIALIZED, ...)` in `MetadataStore`.
    4. Returns `InitUploadResponse(upload_id, upload_url, upload_url_expires_at, status)`.
  - `GET /uploads/{upload_id}` — requires `require_principal`, no CSRF (read-only).
    Returns 404 for unknown IDs or uploads not owned by the caller.
  - `POST /uploads/{upload_id}/complete` — requires `require_principal` + `require_csrf`.
    Transitions status to COMPLETED; returns 404 for unknown/unowned uploads.
- Wired `uploads_router` into `src/urdu_pipeline/api/app.py`.
- Wrote `tests/unit/test_upload_routes.py` (32 tests) BEFORE implementation:
  - Confirmed all 32 tests failed at the start (404 from missing routes).
  - Auth: 401 without credentials; 403 with session but no CSRF; 200 with CSRF; bearer bypasses CSRF.
  - Validation: disallowed extension → 422; disallowed content_type → 422; size 0 → 422;
    size > 500 MB → 422; unknown field → 422.
  - No leakage: response contains `upload_id` and `upload_url` but not `user_id` or `object_key`.
  - Ownership: another user's upload returns 404 (not 403) to avoid resource enumeration.
  - Status transitions: init → "initialized"; complete → "completed".
- Test count: 500 passed, 3 skipped.

Why it was needed:

- Provides the entry point for all audio uploads — clients cannot start a run without a
  completed upload record.
- Signed URL pattern keeps audio bytes out of the API server (direct client-to-object-store PUT),
  reducing latency and memory pressure on the API.
- Internal object key derivation (`uploads/{upload_id}`) ensures the storage topology is
  opaque to callers; it can change without breaking the API contract.
- Ownership checks on GET and complete prevent upload ID enumeration attacks.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 5.2.4: Run Translation And Article Generation

What was done:

- Wrote `tests/unit/test_processor_pipeline.py` (12 tests) before any
  implementation, covering all paths under TDD. Tests confirmed failing first.

- Created `src/urdu_pipeline/processor/pipeline.py` with:
  - `run_translation_and_article(job_record, reconciled_artifact, *,
    artifact_repo, usage_ledger, budget_service, translator_fn, article_fn)
    -> tuple[ArtifactReference, ArtifactReference]`
    - `_enforce_budget(job_record, budget_service, stage)` — calls
      `budget_service.check_run_budget(next_cost_usd=0.0)` (current total only)
      before each stage; raises `FatalJobError` with a descriptive message if
      `decision.blocked`.
    - Calls `translator_fn(reconciled_artifact)` →
      `(EnglishTranslationArtifact, usage_dict)`.
    - Records translation usage via `_record_stage_usage` → `usage_ledger`.
    - Persists translation artifact (`stage=TRANSLATOR`,
      `artifact_type=ENGLISH_TRANSLATION`).
    - Calls `_enforce_budget` again — now includes translation cost, so if
      translation was expensive the article stage is blocked first.
    - Calls `article_fn(translation_artifact)` →
      `(ArticleArtifact, usage_dict)`.
    - Records article usage via `_record_stage_usage` → `usage_ledger`.
    - Persists article artifact (`stage=ARTICLE_GENERATOR`,
      `artifact_type=FINAL_ARTICLE`).
    - Returns `(translation_ref, article_ref)`.
  - Both `translator_fn` and `article_fn` return `(artifact, usage_dict)`
    where `usage_dict` contains `model_id`, `cost_usd`, and `actual_usage`.

- Tests cover:
  - Returns two `ArtifactReference` instances.
  - Translation ref has `stage=TRANSLATOR`, `artifact_type=ENGLISH_TRANSLATION`.
  - Article ref has `stage=ARTICLE_GENERATOR`, `artifact_type=FINAL_ARTICLE`.
  - Exactly 2 artifacts saved to repo.
  - Translation payload contains `full_text_english`.
  - Article payload contains `article.title`.
  - Artifact `user_id`/`run_id` match the job.
  - 2 usage records persisted (one per stage).
  - Usage records have correct `user_id`/`run_id`/`job_id`.
  - Budget exceeded before translation → `FatalJobError`, `translator_fn` never
    called (verified via call_log).
  - Translation runs (budget OK), costly translation records exceed cap → article
    stage blocked before `article_fn` is called.
  - Prompt safety: Urdu source text does not appear as a key in the translation
    artifact payload.

- Test count: 765 passed at end of step (prior to 5.3.1).

Why it was needed:

- Translation and article generation are the most expensive stages. Checking
  budget with the current ledger total before each stage ensures cost overruns
  stop processing immediately without wasting more provider calls.
- The shared `usage_ledger`/`budget_service` linkage enables the mid-run
  blocking test: translation records $0.12, the next budget check reads $0.12 >
  $0.10 cap, article is blocked.
- Injectable `translator_fn`/`article_fn` follow the same pattern as the other
  processor modules, making provider switching (Step 5.3+) a closure swap.

### Step 5.3.1: Add Idempotent Retry Behavior

What was done:

- Wrote `tests/unit/test_processor_idempotency.py` (17 tests) before any
  implementation, confirming failure with `ModuleNotFoundError`. Tests cover:
  - `find_stage_artifact` — returns matching ref or `None`.
  - `stage_usage_key` — deterministic, differs by stage/chunk, differs by run.
  - `UsageRecord.idempotency_key` field — defaults to `None`, accepts string.
  - `InMemoryUsageLedger.record_usage` deduplication — same key is a no-op,
    different keys all recorded, no-key records always appended.
  - `run_chunker_stage` — skips stage (no `chunker_fn` call, no save) when
    CHUNK_MANIFEST already present in repo; only 1 save on second call.
  - `run_transcription_and_reconciliation` — skips when both RAW_URDU_TRANSCRIPT
    + RECONCILED_URDU_TRANSCRIPT exist; only 2 saves on retry (not 4).
  - `run_translation_and_article` — skips when both ENGLISH_TRANSLATION +
    FINAL_ARTICLE exist; only 2 saves on retry (not 4).

- Created `src/urdu_pipeline/processor/idempotency.py` with:
  - `find_stage_artifact(refs, stage, artifact_type) -> ArtifactReference | None`
    Scans a list of `ArtifactReference` objects (from `list_run_artifacts`) and
    returns the first match or `None`.
  - `stage_usage_key(run_id, stage_label, item_id=None) -> str`
    Returns a deterministic colon-separated key
    (`"{run_id}:{stage_label}"` or `"{run_id}:{stage_label}:{item_id}"`)
    suitable for `UsageRecord.idempotency_key`.

- Modified `src/urdu_pipeline/application/ports/services.py`:
  - Added `idempotency_key: str | None = None` field to `UsageRecord`
    (frozen dataclass; default `None` preserves all existing call sites).

- Modified `src/urdu_pipeline/infrastructure/in_memory.py`:
  - `InMemoryUsageLedger` gained `_idempotency_keys: set[str]`.
  - `record_usage` checks `record.idempotency_key`: if non-`None` and already
    in the set, returns silently; otherwise adds key to set and appends record.

- Modified `src/urdu_pipeline/processor/chunker.py`:
  - At top of `run_chunker_stage`, calls `artifact_repo.list_run_artifacts()`
    and `find_stage_artifact(…, CHUNKER, CHUNK_MANIFEST)`.
  - Returns existing ref immediately if found (no `chunker_fn` invocation).

- Modified `src/urdu_pipeline/processor/transcriber.py`:
  - Checks for both RAW_URDU_TRANSCRIPT and RECONCILED_URDU_TRANSCRIPT at
    top of `run_transcription_and_reconciliation`; short-circuits if both found.
  - Per-chunk usage records now carry `idempotency_key=stage_usage_key(run_id,
    "transcriber", chunk.chunk_id)`.

- Modified `src/urdu_pipeline/processor/pipeline.py`:
  - Checks for both ENGLISH_TRANSLATION and FINAL_ARTICLE at top of
    `run_translation_and_article`; short-circuits if both found.
  - `_record_stage_usage` gains optional `idempotency_key` parameter, forwarded
    to `UsageRecord`.
  - Translation usage key: `stage_usage_key(run_id, "translator")`.
  - Article usage key: `stage_usage_key(run_id, "article_generator")`.

- Test count: **767 passed** (prior to 5.3.2).

Why it was needed:

- Any processor crash after a stage completes but before the job is marked done
  will trigger a retry. Without idempotency, each retry would re-run already-
  completed stages, doubling artifact records and usage charges.
- `list_run_artifacts` is the read-side guard: stages check their output before
  running. The `idempotency_key` on `UsageRecord` is the write-side guard:
  even if the check races with a concurrent write, the ledger discards
  duplicates.
- The `_ListingRepo` test double (defined in the test file) implements both
  `save_artifact` and `list_run_artifacts`, enabling precise crash-retry
  simulation without requiring a real database.
- All existing stage function tests continue to pass unchanged because
  `_FakeArtifactRepo.list_run_artifacts` returns `[]`, so the new idempotency
  check is a no-op there (no existing artifacts → run normally).

### Step 5.3.2: Add Temporary Object And Workspace Cleanup

What was done:

- Wrote `tests/unit/test_processor_cleanup.py` (14 tests) before any
  implementation, confirming failure with `ModuleNotFoundError`. Tests cover:
  - `cleanup_run_tmp_objects` calls `delete_prefix` with a prefix that contains
    both `user_id` and `run_id` and starts with `tmp/`.
  - Returns the deleted object count from `delete_prefix`.
  - Returns 0 when nothing was deleted.
  - Different runs produce different prefixes (no cross-run deletion).
  - Integration test with `InMemoryObjectStore`: 2 run-scoped objects deleted,
    unrelated `tmp/other/key` preserved; verified via `list_prefix`.
  - `cleanup_workspace` removes the workspace root directory.
  - `cleanup_workspace` is safe when the directory does not exist.
  - `cleanup_workspace` calls `workspace.cleanup()` exactly once.
  - `cleanup_after_run(is_retry=False)` calls `delete_prefix` and workspace
    cleanup (covers success and fatal failure paths).
  - `cleanup_after_run(is_retry=True)` does NOT call `delete_prefix` (tmp
    objects preserved for retry), but still calls workspace cleanup.
  - `cleanup_after_run` workspace cleanup runs even when `delete_prefix` raises
    (verified by checking `cleanup_calls == 1` after `RuntimeError`).

- Created `src/urdu_pipeline/processor/cleanup.py` with:
  - `cleanup_run_tmp_objects(job_record, *, object_store, key_builder=None) -> int`
    Calls `object_store.delete_prefix(f"tmp/users/{user_id}/runs/{run_id}/")`.
    Returns the number of deleted objects.
  - `cleanup_workspace(workspace) -> None`
    Calls `workspace.cleanup()` (delegates to `FilesystemRunWorkspace.cleanup`,
    which removes the workspace root via `shutil.rmtree`).
  - `cleanup_after_run(job_record, *, workspace, object_store, is_retry, key_builder=None) -> None`
    `try` block deletes tmp objects when `not is_retry`.
    `finally` block always calls `cleanup_workspace`.
    This guarantees workspace cleanup even when the object store is unavailable.

- Test count: **781 passed**.

Why it was needed:

- Chunk files uploaded to `tmp/` accumulate per run attempt; without cleanup
  they would grow indefinitely, incurring ongoing storage charges.
- The `is_retry` distinction is critical: on a transient failure the chunks are
  preserved so the idempotent chunker stage (Step 5.3.1) can skip re-uploading
  them on the next attempt. On a successful or permanently failed run they can
  be safely deleted because no future attempt will need them.
- Local workspace directories are ephemeral and per-attempt. Keeping them would
  exhaust disk space on the processor host. The `finally` guarantee ensures
  they are always cleaned regardless of object store availability.
- `_FakeWorkspace` (defined in the test file) tracks `cleanup_calls`, enabling
  precise assertions without real filesystem dependencies.

Provider-switch relevance:

- `translator_fn` and `article_fn` are the exact injection points for
  alternative providers (Gemini, Llama, etc.) — the budget enforcement and
  artifact persistence logic stays unchanged.

### Step 5.2.3: Run Transcription And Reconciliation

What was done:

- Wrote `tests/unit/test_processor_transcriber.py` (13 tests) before any
  implementation, covering all paths under TDD. Tests confirmed failing first.

- Created `src/urdu_pipeline/processor/transcriber.py` with:
  - `run_transcription_and_reconciliation(job_record, chunk_manifest, *,
    metadata_store, artifact_repo, usage_ledger, chunk_transcriber_fn,
    reconciler_fn) -> tuple[ArtifactReference, ArtifactReference]`
    - Iterates through `chunk_manifest.chunks` in chunk_index order.
    - **Before each chunk**: calls `_check_cancellation(job_record,
      metadata_store)` — reads `metadata_store.get_job_by_id` and raises
      `FatalJobError` if status is `CANCELLED`.
    - Calls `chunk_transcriber_fn(chunk)` → `TranscriptionResult`.
    - Appends `RawTranscriptChunk` to the accumulator.
    - Records usage via `usage_ledger.record_usage(UsageRecord(...))` with
      `user_id`, `run_id`, `job_id`, `model_id`, and `cost_usd` from
      `result.actual_usage["cost_usd"]`.
    - Builds `RawTranscriptArtifact` from all chunk results.
    - Persists raw artifact via `artifact_repo.save_artifact(stage=TRANSCRIBER,
      artifact_type=RAW_URDU_TRANSCRIPT)`.
    - Calls `reconciler_fn(raw_artifact)` → `ReconciledTranscriptArtifact`.
    - Persists reconciled artifact via `artifact_repo.save_artifact(
      stage=TRANSCRIPT_RECONCILER, artifact_type=RECONCILED_URDU_TRANSCRIPT)`.
    - Returns `(raw_ref, reconciled_ref)`.
  - `_check_cancellation(job_record, metadata_store)` — isolated helper that
    can be called at the start of the loop for each chunk.
  - `_build_raw_artifact(chunk_manifest, raw_chunks)` — assembles the
    `RawTranscriptArtifact` with a fresh `ArtifactManifest`.

- Both `chunk_transcriber_fn` and `reconciler_fn` are injectable `Callable`
  parameters, keeping the module unit-testable without ffmpeg or the network.

- Tests cover:
  - Returns two `ArtifactReference` instances.
  - Raw ref has `stage=TRANSCRIBER`, `artifact_type=RAW_URDU_TRANSCRIPT`.
  - Reconciled ref has `stage=TRANSCRIPT_RECONCILER`,
    `artifact_type=RECONCILED_URDU_TRANSCRIPT`.
  - Two artifacts saved to repo (raw + reconciled).
  - Raw payload contains `chunks` list (3 chunks verified).
  - Reconciled payload contains non-empty `full_text_urdu`.
  - Artifact `user_id`/`run_id` matches the job.
  - Usage recorded for each chunk (3 chunks → 3 usage records).
  - Usage records have correct `user_id`, `run_id`, `job_id`.
  - Zero chunks: no usage records.
  - Pre-cancellation (CANCELLED before call): raises `FatalJobError`,
    transcriber never called.
  - Mid-run cancellation (cancelled after chunk 1): chunk 1 transcribed,
    chunk 2 stopped before transcription.
  - Zero chunks: two artifacts still saved (empty).

- Test count: 753 passed, 3 skipped.

Why it was needed:

- Transcription is the core value-generating step. Wrapping it in the processor
  with cancellation polling ensures long-running transcription jobs can be
  interrupted cleanly.
- Per-chunk usage recording keeps the cost ledger accurate even when jobs fail
  partway through.
- The injectable `chunk_transcriber_fn` / `reconciler_fn` pattern allows the
  same orchestration logic to be tested without a real audio provider.

Provider-switch relevance:

- `chunk_transcriber_fn` is the exact injection point where a different AI
  provider (e.g. Gemini, Whisper local, etc.) can be plugged in without
  changing the processor orchestration logic.

### Step 5.2.2: Run Chunker And Persist Chunk Manifest

What was done:

- Wrote `tests/unit/test_processor_chunker.py` (14 tests) before any
  implementation, covering all paths under TDD. Tests confirmed failing first.

- Created `src/urdu_pipeline/processor/chunker.py` with:
  - `run_chunker_stage(job_record, audio_path, *, workspace, object_store,
    artifact_repo, chunker_fn, key_builder=None) -> ArtifactReference`
    - Calls `chunker_fn(audio_path)` — a `Callable[[Path], ChunkManifestArtifact]`
      injectable dependency; production code wraps `ChunkerStage.run`, tests use
      a fake that creates real chunk files without invoking ffmpeg.
    - For each chunk, uploads the chunk file at `workspace.root / chunk.file_path`
      to object store under the opaque key from `ObjectKeyBuilder.run_chunk`:
      `tmp/users/{user_id}/runs/{run_id}/chunks/{chunk_id}.{audio_ext}`.
    - Calls `artifact_repo.save_artifact(stage=CHUNKER, artifact_type=CHUNK_MANIFEST,
      payload=manifest.model_dump())` to persist the manifest JSON.
    - Returns the `ArtifactReference` from the repository.
    - Any exception from `chunker_fn` (including `FatalJobError`) propagates
      unchanged so `claim_and_run` routes it correctly.

- Tests cover:
  - Returns `ArtifactReference` with `stage=CHUNKER`, `artifact_type=CHUNK_MANIFEST`.
  - `artifact_repo.save_artifact` called once with correct user_id/run_id.
  - Manifest payload contains the chunk list (verified for 3 chunks).
  - All chunk files are uploaded to object store (verified for 3 chunks).
  - Uploaded chunk content matches the file bytes.
  - Chunk keys start with `tmp/` (scoped under transient namespace).
  - Chunk keys include the run_id (user-scoped isolation).
  - Workspace temp-dir path is absent from chunk keys.
  - Original user filename is absent from chunk keys.
  - Zero chunks: no uploads occur but manifest is still saved.
  - `FatalJobError` from `chunker_fn` propagates unchanged.
  - Unexpected `RuntimeError` from `chunker_fn` propagates unchanged.

- One test assertion was refined during red→green: the original assertion
  `"chunk_0001.mp3" not in key` was wrong — the internally-generated chunk_id
  (`chunk_0001`) legitimately appears in the opaque key. Split into two tests:
  workspace-path-absent and original-user-filename-absent.

- Test count: 740 passed, 3 skipped.

Why it was needed:

- The chunker produces multiple local files (one per audio segment) that need
  to be in object storage before the transcription stage can read them on any
  worker node.
- Uploading under `tmp/users/{user_id}/runs/{run_id}/chunks/` scopes all
  transient objects so they can be cleaned up as a prefix after the run.
- Persisting the `ChunkManifestArtifact` via `ArtifactRepository` (not raw
  metadata store) keeps the artifact ownership and retrieval consistent with
  the API artifact routes from Stage 4.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 5.2.1: Materialize Workspace And Validate Audio

What was done:

- Wrote `tests/unit/test_processor_workspace.py` (25 tests) before any
  implementation, covering all paths under TDD.

- Created `src/urdu_pipeline/processor/workspace.py` with:
  - `AudioValidationError(FatalJobError)` — concrete fatal error for
    unprocessable audio (missing ffprobe, corrupt file, zero duration).
  - `_upload_object_key(upload_id)` — derives `"uploads/{upload_id}"` to match
    the key written by the API upload routes exactly.
  - `_safe_input_filename(original_filename)` — strips directory parts,
    replaces non-alphanumeric characters with underscores, and falls back to
    `"audio.bin"` for empty or dot-leading results.
  - `materialize_upload(job_record, *, metadata_store, object_store, workspace)`
    — looks up `RunRecord → upload_id → UploadRecord`, derives the object key,
    streams the binary from `ObjectStore.get_stream` to
    `workspace.input_path(safe_filename)`, and returns the local `Path`.
    Raises `FatalJobError` if the run has no upload_id, if the upload record
    is missing, or if the object cannot be read from the store.
  - `validate_audio(audio_path)` — runs `ffprobe -show_entries format=duration
    -of json`, parses the output, and returns the duration in seconds.
    Raises `AudioValidationError` for FileNotFoundError (ffprobe absent),
    CalledProcessError (non-zero exit), missing/zero/negative duration, or
    unparseable JSON output.

- Tests cover:
  - Happy path: bytes written correctly, correct filename, path inside workspace.
  - Original filename propagation and fallback when `original_filename` is None.
  - Traversal-style filenames sanitized (`../../etc/passwd.mp3` → `passwd.mp3`).
  - Unicode and space characters in filenames sanitized safely.
  - `FatalJobError` raised when run not found, run has no upload_id, upload
    record missing, or object missing from object store.
  - `_safe_input_filename` unit tests: directory stripping, unsafe-char
    replacement, fallback for empty result, single-component guarantee.
  - `validate_audio` with mocked ffprobe: returns float duration, raises on
    non-zero exit, FileNotFoundError, zero duration, negative duration, missing
    duration key, and unparseable JSON.
  - `AudioValidationError` confirmed to be a subclass of `FatalJobError`.
  - Workspace cleanup removes scratch files.

- Test count: 726 passed, 3 skipped.

Why it was needed:

- The processor cannot run any pipeline stage (chunker, transcriber, …) until
  the audio file exists locally. Materializing the upload bridges the gap
  between the API-side object-store write and the processor-side execution.
- Validating with `ffprobe` before the expensive chunker+transcription pipeline
  surfaces corrupt-file failures cheaply and immediately, rather than inside a
  long-running stage.
- `AudioValidationError` extends `FatalJobError` so `claim_and_run` marks the
  job terminal without retrying an intrinsically unprocessable input.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 5.1.2: Implement Claim, Heartbeat, Lease, Retry, Cancel, Dead-Letter

What was done:

- Wrote `tests/unit/test_processor_lifecycle.py` (28 tests) before any
  implementation, covering all lifecycle paths.

- Extended `JobQueue` protocol in `services.py` with `complete(lease)` — the
  success acknowledgement that removes the lease and makes the job terminal,
  preventing duplicate delivery.

- Extended `MetadataStore` protocol with `get_job_by_id(job_id)` (processor
  lookup without user_id ownership check) and `update_job(record)`.

- Implemented both in `InMemoryMetadataStore` and `InMemoryJobQueue`
  (`in_memory.py`):
  - `InMemoryJobQueue.complete`: removes lease, adds to `_completed_jobs`,
    `_is_terminal` returns True for completed jobs.
  - `InMemoryMetadataStore.get_job_by_id`: direct dict lookup (no user_id).
  - `InMemoryMetadataStore.update_job`: replaces the stored record.

- Updated `RedisJobQueue` in `redis_queue.py` with `complete(lease)` that
  delegates to `metadata_store.complete_job(lease)`.

- Updated `FakeAuthoritativeJobs` in `tests/unit/test_redis_job_queue.py`
  with `complete_job` to satisfy the `JobQueue` protocol check.

- Updated `FakeJobQueue` in `tests/unit/test_service_ports.py` with `complete`
  and added `"complete"` to the `required_methods` set.

- Created `src/urdu_pipeline/processor/lifecycle.py` with:
  - `TransientJobError` — handler raises to request retry.
  - `FatalJobError` — handler raises to abort immediately.
  - `heartbeat(queue, lease, *, lease_seconds)` — thin `extend_lease` wrapper
    for background-thread heartbeating.
  - `claim_and_run(queue, metadata_store, *, worker_id, handler, ...)`:
    - Returns `False` when queue is empty.
    - Returns `True` for all other outcomes (success, retry, failure, cancel).
    - Sets `JobRecord.status = RUNNING` and `RunRecord.status = RUNNING` before
      invoking the handler.
    - **Success**: status → SUCCEEDED; `RunRecord.status` → SUCCEEDED; lease
      completed in queue.
    - **TransientJobError, attempt < max**: status → QUEUED; re-enqueued.
    - **TransientJobError, attempt >= max**: status → FAILED; `RunRecord`
      → FAILED; dead-lettered in queue.
    - **FatalJobError or unexpected exception**: status → FAILED; `RunRecord`
      → FAILED; terminal failure in queue.
    - **Pre-cancelled job**: `queue.cancel` called; handler not invoked.
    - **Missing job record**: terminal failure in queue; handler not invoked.

- Test count: 701 passed, 3 skipped.

Why it was needed:

- The processor needs deterministic, testable lifecycle semantics independent
  of any queue implementation before pipeline execution is layered on top.
- `complete()` closes the "success acknowledgement" gap in the `JobQueue` port
  (the protocol had claim/retry/fail/dead-letter but no success path).
- `get_job_by_id` and `update_job` give the processor full metadata access
  without duplicating the ownership-check logic that belongs to the API layer.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 5.1.1: Add Processor Command And Service Auth

What was done:

- Wrote `tests/unit/test_service_auth.py` (23 tests) before any implementation:
  - **AppState field**: `service_auth_token` kwarg accepted; defaults to `None`.
  - **Internal endpoint — happy path**: valid service token → 200, body contains
    `status=ok`, `principal_kind=service`, and `scopes=["processor"]`.
  - **Internal endpoint — rejections**: no auth header → 401; wrong token → 401;
    service auth not configured → 401; empty bearer value → 401; non-Bearer scheme → 401.
  - **User credentials blocked on internal endpoint**: session cookie only → 401;
    user bearer token → 401 (user tokens are not the service token).
  - **Service token blocked on user endpoints**: service token on `GET /runs` → 401;
    on `POST /uploads/init` → 401; on `POST /runs` → 401; on `GET /runs/{id}/artifacts` → 401.
    (Service tokens are not stored in the user bearer store, so bearer lookup returns
    `None` → 401 from `require_principal`.)
  - **Timing safety**: token with leading space rejected; token with trailing newline rejected
    (``secrets.compare_digest`` does exact byte comparison).
  - **Processor CLI command**: `process --help` exits 0; `process` without token exits non-zero;
    `process --dry-run` with token exits 0 and reports "valid".

- Implementation changes:
  - `src/urdu_pipeline/api/dependencies.py`:
    - `AppState` extended with `service_auth_token: str | None = None` and
      `service_identity_id: ServiceIdentityId = field(default_factory=ServiceIdentityId.new)`.
    - Added `get_principal_from_service_token` dependency: checks
      `Authorization: Bearer <X>` against `state.service_auth_token` using
      ``secrets.compare_digest`` (constant-time).  Returns `AuthPrincipal(kind="service",
      scopes=frozenset({"processor"}))` on match, `None` otherwise.
    - Added `require_service_principal` dependency: wraps the above, raises 401.
  - `src/urdu_pipeline/api/routes/internal.py` *(NEW FILE)*: `GET /internal/ping`
    returns `{"status": "ok", "principal_kind": ..., "scopes": [...]}` protected
    by `require_service_principal`.
  - `src/urdu_pipeline/api/app.py`: `internal_router` included (before public routes).
  - `src/urdu_pipeline/config/settings.py`: `service_auth_token: str | None` field added.
  - `src/urdu_pipeline/cli.py`: `process` command added — validates `SERVICE_AUTH_TOKEN`,
    supports `--dry-run` for config validation, prints placeholder message for the
    not-yet-implemented job loop (Stage 5.1.2+).
  - `.env.example`: `SERVICE_AUTH_TOKEN=` entry added with generation instructions.

- Test count: 673 passed, 3 skipped.

Why it was needed:

- The processor runs as a separate process and needs a way to call internal API
  endpoints without being a user.  A static shared secret (not in the bearer token
  store) kept in `AppState`/settings is the simplest cloud-neutral mechanism.
- Constant-time comparison (``secrets.compare_digest``) prevents timing-oracle attacks
  on the service token.
- The processor command skeleton (`urdu-pipeline process`) gives operators a clear
  entry point; `--dry-run` lets deployment scripts validate token config before
  starting the real loop.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 4.3.5: Generate And Review OpenAPI Schema

What was done:

- Wrote `tests/unit/test_openapi_schema.py` (35 tests) to validate key schema properties:
  - Structural: `openapi`, `info`, `paths` keys present; version starts with "3."; title non-empty.
  - Route presence (parametrized): all 20 public paths and their HTTP methods are documented —
    health, auth (login, logout), tokens (POST/GET/DELETE), all upload variants (init, complete,
    multipart init/parts/complete/abort, direct), runs (POST/GET/cancel/events/artifacts),
    and artifacts (GET/download).
  - No internal identifier leakage: `user_id`, `job_id`, `object_key`, `key` absent from all
    component schema property names.
  - No provider/model/prompt fields: `provider`, `model_id`, `prompt`, `api_key` absent.
  - Token security: `TokenListResponse` and `TokenSummary` do not expose the raw `token` value.
  - Request schema policy: `InitUploadRequest` and `CreateRunRequest` do not accept `user_id`,
    `object_key`, `provider`, `model_id`, or `prompt`.
  - Snapshot: `test_openapi_snapshot_is_saved` writes `docs/openapi.json` (2,443 lines) for
    human review and future diffing — always passes.
- All 35 tests passed without any schema fixes needed, confirming the API surface is clean.
- Schema summary:
  - 20 paths (all public — no accidental internal route exposure)
  - 33 component schemas (request/response/enum types only)
  - Notable schema: `Body_direct_upload_uploads_direct_post` (FastAPI auto-generated name
    for the UploadFile parameter on POST /uploads/direct — cosmetically ungainly but correct)
- Test count: 650 passed, 3 skipped.

Why it was needed:

- A machine-readable schema is the primary integration contract for frontend and other
  consumer teams.  Validation tests ensure the contract stays clean as routes evolve.
- The no-leakage assertions provide a regression gate: any future change that accidentally
  adds `user_id`, `object_key`, or `provider` to a schema is caught immediately.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 4.3.4: Add Artifact Routes

What was done (strict TDD — tests written and confirmed failing before implementation):

- Extended `ArtifactRecord` in `src/urdu_pipeline/application/ports/services.py`:
  - Added `has_markdown: bool = False` — whether a Markdown version of the artifact exists.
- Added `list_run_artifacts(*, user_id, run_id) -> Sequence[ArtifactRecord]` to `MetadataStore`.
- Implemented `list_run_artifacts` in `InMemoryMetadataStore`: verifies run ownership before
  listing, returns artifacts sorted by `(created_at, artifact_id)`.
- Created `src/urdu_pipeline/api/routes/artifacts.py` with two FastAPI routers:
  - `runs_router` (prefix `/runs`):
    - `GET /runs/{run_id}/artifacts` — ownership-checked via `get_run`; returns
      `ArtifactListResponse(artifacts: [ArtifactSummary])` with no object keys or user_id.
  - `artifacts_router` (prefix `/artifacts`):
    - `GET /artifacts/{artifact_id}` — ownership-checked via `get_artifact`; returns
      `ArtifactSummary`. Returns 404 for unknown/other-user artifacts.
    - `GET /artifacts/{artifact_id}/download?format=json|markdown` — ownership-checked;
      derives object key as `artifacts/{artifact_id}.json` or `artifacts/{artifact_id}.md`;
      calls `ObjectStore.create_signed_download_url`; returns `ArtifactDownloadResponse`
      (artifact_id, download_url, expires_at, format). Returns 404 when
      `format=markdown` but `has_markdown=False`.
- Wired both routers into `src/urdu_pipeline/api/app.py`:
  - `artifact_runs_router` is registered BEFORE `runs_router` so the
    `/runs/{run_id}/artifacts` literal path is resolved before the generic `/{run_id}` handler.
  - `artifacts_router` is registered separately.
- Wrote `tests/unit/test_artifact_routes.py` (29 tests) BEFORE implementation:
  - Confirmed all 29 failed at start (404, routes absent).
  - Artifacts are seeded directly into both the metadata store and object store in tests,
    simulating what the Stage 5 processor would produce.
  - List: auth, empty list, populated list, 404 for unknown/other-user run, no key leakage.
  - Get: auth, 200 metadata, 404 for unknown/other-user, no key or user_id in response.
  - Download: auth, JSON/markdown URLs, expires_at in future, 404 for unknown/other-user,
    404 for markdown when has_markdown=False, 422 for invalid format, no object key in response.
- Test count: 615 passed, 3 skipped.

Why it was needed:

- Artifacts are the final output of the pipeline; without read and download routes, callers
  have no way to retrieve processed transcripts, translations, or articles.
- Signed URLs keep artifact content out of the API response body, reducing latency and
  preventing the API server from becoming a bandwidth bottleneck.
- The `has_markdown` flag enables the API to guard against requests for non-existent Markdown
  versions, returning a clear 404 rather than letting the object-store request fail silently.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 4.3.3: Add Run Routes

What was done (strict TDD — tests written and confirmed failing before implementation):

- Extended `RunRecord` in `src/urdu_pipeline/application/ports/services.py`:
  - Added `upload_id: UploadId | None = None` — tracks which upload this run processes.
  - Added `description: str | None = None` — user-supplied label, echoed in responses.
- Added `update_run(record: RunRecord) -> None` to the `MetadataStore` protocol.
- Implemented `update_run` in `InMemoryMetadataStore` (raises `KeyError` for unknown run IDs).
- Extended `AppState` in `src/urdu_pipeline/api/dependencies.py`:
  - Added `job_queue: JobQueue | None = None` — optional; existing tests do not need a queue.
  - Added `get_job_queue` dependency.
  - Imported `JobQueue` from `application.ports`.
- Created `src/urdu_pipeline/api/routes/runs.py` with five routes:
  - `POST /runs` — validates upload ownership and status (must be COMPLETED → 422 otherwise),
    creates `RunRecord(status=PENDING)`, creates `JobRecord(status=QUEUED)`, enqueues a
    `QueueMessage` if a `JobQueue` is configured. Returns `RunResponse` with no user_id or job_id.
  - `GET /runs` — lists all runs owned by the caller; returns `RunListResponse(runs, total)`.
  - `GET /runs/{run_id}` — ownership-checked run lookup; 404 for unknown/other-user.
  - `GET /runs/{run_id}/events` — ownership-checked; returns `EventListResponse(events=[])` (event
    persistence is Stage 5 work — this is a deliberate stub).
  - `POST /runs/{run_id}/cancel` — ownership-checked; transitions status to CANCELLED via
    `update_run`; returns `CancelRunResponse(run_id, status)`. Requires CSRF.
- Wired `runs_router` into `src/urdu_pipeline/api/app.py`.
- Wrote `tests/unit/test_run_routes.py` (35 tests) BEFORE implementation:
  - Confirmed all 35 tests failed at the start (`AppState` rejected `job_queue` kwarg).
  - Create: auth, CSRF, bearer bypass, returns run_id/upload_id/status/description, no
    user_id or job_id, unknown upload → 404, other user's upload → 404,
    non-completed upload → 422, unknown field → 422, queue enqueue verified via
    `len(job_queue._queued) == 1`.
  - List: auth, empty list, lists caller's runs, excludes other users' runs.
  - Get: auth, 200/404 for own/unknown/other-user, no user_id or job_id in response.
  - Events: auth, returns 200 with `events` list, 404 for unknown/other-user run.
  - Cancel: auth, CSRF, 200, status=cancelled, 404 for unknown/other-user.
- Test count: 586 passed, 3 skipped.

Why it was needed:

- Runs are the central resource in the pipeline: every audio file must be processed through a
  run before artifacts are produced.
- Enforcing COMPLETED upload status at run-creation time prevents the processor from receiving
  jobs with unavailable input data.
- Queueing the job immediately on run creation decouples the API from the processor: the
  processor can scale independently and pick up jobs via the queue.
- The events stub endpoint fulfils the API contract so clients can poll for progress without
  requiring Stage 5 to be complete first.

Provider-switch relevance:

- Not required for CLI provider switching.

### Step 4.3.2: Add Multipart And Direct Upload Routes

What was done (strict TDD — tests written and confirmed failing before implementation):

- Extended `UploadRecord` in `src/urdu_pipeline/application/ports/services.py`:
  - Added `multipart_upload_id: str | None = None` — the object store's internal multipart
    upload handle ID. Never exposed in API responses; used server-side to reconstruct the
    `MultipartUpload` struct for complete/abort/part-URL operations.
- Refactored `src/urdu_pipeline/api/schemas.py`:
  - Extracted `_BaseUploadRequest` with shared `field_validator` logic for `filename`,
    `content_type`, and `size_bytes`. Both `InitUploadRequest` and new
    `InitMultipartUploadRequest` inherit from it.
  - Added `MAX_DIRECT_UPLOAD_BYTES = 50 MB` and `MAX_PART_NUMBER = 10,000`.
  - Added `InitMultipartUploadRequest` (same constraints as single-part init).
  - Added `InitMultipartUploadResponse(upload_id, part_url, part_url_expires_at, status)`.
  - Added `PartUrlResponse(upload_id, part_number, part_url, part_url_expires_at)`.
  - Added `CompleteMultipartRequest(parts: list[UploadPartInfo])` with a non-empty validator.
- Replaced `src/urdu_pipeline/api/routes/uploads.py` with the full route set:
  - `POST /uploads/multipart/init` — starts multipart upload via `ObjectStore.create_multipart_upload`,
    returns signed URL for part 1, stores `UploadRecord(status=UPLOADING, multipart_upload_id=...)`.
  - `GET /uploads/multipart/{upload_id}/parts/{part_number}` — returns a fresh signed URL for
    any part (1–10,000); validates part_number and enforces ownership (404 for unknown/other user).
  - `POST /uploads/multipart/{upload_id}/complete` — calls `ObjectStore.complete_multipart_upload`
    with caller-supplied ETags, transitions status to COMPLETED. Requires non-empty parts.
  - `DELETE /uploads/multipart/{upload_id}` — calls `ObjectStore.abort_multipart_upload`,
    transitions status to CANCELLED.
  - `POST /uploads/direct` — accepts `UploadFile` (multipart/form-data), validates extension
    and content_type (same allowlists as init), enforces 50 MB ceiling (returns 413 over limit),
    rejects empty files (422), streams bytes to `ObjectStore.put_stream`, stores
    `UploadRecord(status=COMPLETED)` in a single round-trip.
  - All existing single-part routes preserved: `POST /uploads/init`, `GET /uploads/{upload_id}`,
    `POST /uploads/{upload_id}/complete`.
  - Route ordering: literal-path segments (multipart/*, direct) are declared before parameterised
    `/{upload_id}` routes to prevent path parameter shadowing.
- Added `python-multipart>=0.0.9,<1` to `pyproject.toml` (required by FastAPI for `UploadFile`).
- Wrote `tests/unit/test_multipart_upload_routes.py` (51 tests) BEFORE implementation:
  - Confirmed all 51 tests failed at the start (routes absent).
  - Multipart init: auth, CSRF, bearer bypass, response fields, no key leakage, all validation rules.
  - Part URL: auth, 200 with signed URL, 404 for unknown/other-user, part-size constraints
    (0, negative, > 10,000 → 400).
  - Complete: auth, CSRF, non-empty parts required, 422 for empty/missing parts, 404 ownership.
  - Abort: auth, CSRF, status=cancelled, 404 ownership.
  - Direct upload: auth, CSRF, status=completed, no key leakage, extension/content_type policy
    parity with init, empty file → 422, > 50 MB → 413.
- Test count: 551 passed, 3 skipped.

Why it was needed:

- Multipart uploads allow clients to upload large audio files reliably by splitting them into
  independently-signed, retriable parts (mirrors the S3/R2 multipart API).
- The abort endpoint ensures in-progress multipart uploads can be cancelled, freeing object-store
  resources from orphaned parts.
- Direct upload provides a simpler one-shot path for small files and automated tests without
  requiring a separate "complete" step.
- Policy parity between all three upload paths (init, multipart, direct) ensures no bypass
  of the extension and MIME-type allowlists regardless of how the client chooses to upload.

Provider-switch relevance:

- Not required for CLI provider switching.

## Completed Stage 4: Auth And API Backend

Purpose:

Build the public backend API resource routes and security controls.

Status:

- Complete through Step 4.3.5.
- No remaining Stage 4 work in the original stepwise plan.

## Completed Stage 5: Processor And Job Execution

Purpose:

Build the long-running worker foundation that claims jobs and executes pipeline
stages.

Status:

- Complete through Step 5.3.2.
- No remaining Stage 5 work in the original stepwise plan.

## Stage 6: Full Local Parity Stack

Purpose:

Make Docker Compose behave like the target backend stack.

Status:

- Complete through Step 6.2.3.
- No remaining Stage 6 work in the original stepwise plan.

Why needed:

- Provides local production-like testing and onboarding.

Needed for provider switch?

- No.

## Stage 7: Operational Hardening

Purpose:

Make the backend operable and safer under failure.

Remaining phases:

- backup/restore/operator docs

Why needed:

- Required before production operation.
- Protects secrets, user data, object storage, DB, queue, and provider spend.

Needed for provider switch?

- No.

### Step 7.1.1: Harden Structured Logging And Redaction

What was done (strict TDD - tests written and confirmed failing before
implementation):

- Added `tests/unit/test_logging_redaction.py` covering:
  - top-level sensitive log fields (`api_key`, bearer auth, prompts, raw
    transcripts, translations, article bodies, object keys)
  - nested event/log payloads containing full translations, Urdu transcript
    text, article body markdown, object keys, and unknown long strings
  - preservation of safe operational metadata such as stage, event type, model,
    provider, counts, cache flags, and cost
  - API request logging that records only method/path/status and excludes
    headers, cookies, CSRF values, query strings, request bodies, and response
    bodies
  - processor stage events sanitizing optional messages and payloads before
    adapter persistence
- Added a PostgreSQL metadata-store regression test proving direct
  `record_stage_event` calls sanitize sensitive messages/payloads before
  durable storage.
- Confirmed the new tests failed before implementation:
  - `safe_log_event` leaked authorization, prompt, object-key, and nested
    payload data.
  - `redact_log_fields` did not exist.
  - API app had no request logging hook.
  - processor `_record_event` did not accept/sanitize optional diagnostics.
  - PostgreSQL stage events stored raw message/payload values.
- Extended `src/urdu_pipeline/logging_utils.py`:
  - added recursive `redact_log_fields`
  - added `redact_event_message`
  - updated `safe_log_event` to preserve safe metadata while redacting/summarizing
    secrets, prompts, object keys, raw text, model outputs, full artifact-like
    payload fields, bytes, nested mappings/lists, and unknown free-form strings
- Added API request logging middleware in `src/urdu_pipeline/api/app.py`:
  - emits `api_request` with method, path, and status code only
  - logs 500 status before re-raising unexpected exceptions
- Updated `src/urdu_pipeline/processor/runtime.py`:
  - `_record_event` accepts optional `message` and `payload`
  - payloads/messages are sanitized before `StageEventRecord` creation
- Updated `src/urdu_pipeline/infrastructure/db/metadata.py`:
  - `record_stage_event` sanitizes message and payload before inserting rows
  - existing safe metadata such as `artifact_id` round-trips unchanged

Why it was needed:

- Stage events and operational logs are visible to operators and potentially
  copied into tickets or monitoring tools.
- Raw prompts, source text, translations, article bodies, full artifacts,
  object keys, headers, cookies, bearer tokens, and CSRF tokens must not be
  emitted as diagnostics.
- Centralizing redaction prevents every API route, processor stage, and adapter
  from needing bespoke safety logic.

Provider-switch relevance:

- Not required for switching providers, but protects real provider prompts,
  provider outputs, usage diagnostics, and object-storage details once real
  provider mode is enabled.

### Step 7.1.2: Add Cleanup Scheduler

What was done (strict TDD - tests written and confirmed failing before
implementation):

- Added `tests/unit/test_cleanup_scheduler.py` covering:
  - expired single-part upload scheduling without duplicating tasks
  - expiring uploads and deleting `uploads/{upload_id}` objects
  - abandoned multipart upload abort plus upload status transition to `EXPIRED`
  - terminal-run temporary chunk cleanup under
    `tmp/users/{user_id}/runs/{run_id}/`
  - expired session purge while preserving active sessions
  - revoked bearer-token purge after retention while preserving active tokens
  - failed cleanup retry rescheduling and later success
  - succeeded cleanup tasks not re-running on repeated scheduler invocations
- Confirmed all 8 tests failed before implementation because
  `urdu_pipeline.processor.cleanup_scheduler` did not exist.
- Added `src/urdu_pipeline/processor/cleanup_scheduler.py`:
  - `CleanupSchedulerConfig`
  - `CleanupSchedulerResult`
  - `cleanup_task_id_for`
  - `schedule_cleanup_tasks`
  - `run_due_cleanup_tasks`
  - `run_cleanup_scheduler`
  - deterministic cleanup task IDs using `uuid5` so scheduling is idempotent
  - task types for expiring uploads, deleting terminal-run tmp objects, purging
    expired sessions, and purging revoked bearer tokens
  - retry handling: due tasks are claimed, attempts increment, failures move to
    `RETRYING` with `run_at = now + retry_delay`, and final failures move to
    `FAILED`
- Extended `CleanupTaskRecord` in
  `src/urdu_pipeline/infrastructure/db/metadata.py` with the lifecycle fields
  already present in the SQL migration:
  - `attempts`
  - `max_attempts`
  - `updated_at`
  - `completed_at`
- Extended `InMemoryMetadataStore`:
  - cleanup task create/get/list/claim/status-transition methods
  - expired upload discovery
  - terminal run discovery for tmp object cleanup
  - expired session listing/deletion
  - revoked bearer-token listing/deletion
- Extended `PostgresMetadataStore`:
  - cleanup task lifecycle column persistence and round-trip mapping
  - due task claim with row locking / `FOR UPDATE SKIP LOCKED`
  - cleanup task success/retry/failure transitions
  - expired upload, terminal run, expired session, and revoked token queries
  - expired session and revoked token deletion helpers
- Updated the PostgreSQL metadata-store test fake to preserve the new cleanup
  task lifecycle columns.

Why it was needed:

- Signed upload URLs, abandoned multipart uploads, expired browser sessions,
  revoked API tokens, and temporary run chunks otherwise accumulate indefinitely.
- Cleanup must be retryable and idempotent because object stores, databases, and
  queues can fail independently.
- The deterministic task IDs prevent duplicate cleanup rows from repeated
  scheduler passes for the same resource.

Provider-switch relevance:

- Not required for CLI provider switching.
- Needed before production operation to control storage cost, auth-token
  retention, session hygiene, and cleanup recovery after infrastructure outages.

### Step 7.1.3: Add Failure-Mode Tests

What was done (strict TDD - tests written and confirmed failing before
implementation):

- Added `tests/unit/test_failure_modes.py` covering:
  - database outage before the handler runs
  - queue outage while acknowledging success
  - object-store outage while writing artifacts
  - partial artifact write where JSON succeeds and Markdown fails
  - provider transient failure classification for retry
  - retry exhaustion with redacted dead-letter reason
  - cleanup final failure with observable, redacted error context
- Confirmed the new tests failed before implementation:
  - initial run: `6 failed, 1 passed`
  - the failures exposed unreleased leases after metadata outage, success
    metadata being published before queue acknowledgement, orphaned JSON
    objects after partial artifact writes, missing provider failure classes,
    unredacted dead-letter reasons, and missing cleanup `last_error`.
- Updated `src/urdu_pipeline/processor/lifecycle.py`:
  - metadata setup failures now release the lease for retry before propagating
    the outage
  - queue terminal actions happen before publishing terminal metadata statuses
  - retry/dead-letter/terminal failure reasons are redacted before queue
    persistence
- Updated `src/urdu_pipeline/infrastructure/artifacts.py`:
  - artifact object writes track written keys
  - partial object writes are cleaned up if a later object write fails
  - written objects are also cleaned up if metadata recording fails
- Added provider failure contracts in `src/urdu_pipeline/providers/base.py`:
  - `ProviderError`
  - `ProviderTransientError`
  - `ProviderFatalError`
  - exported through `src/urdu_pipeline/providers/__init__.py`
- Updated processor stage orchestrators:
  - `src/urdu_pipeline/processor/transcriber.py` maps provider transient
    failures to `TransientJobError` and provider fatal failures to
    `FatalJobError`
  - `src/urdu_pipeline/processor/pipeline.py` applies the same mapping to
    translation and article-generation provider calls
  - provider failure messages are redacted before they reach lifecycle retry
    handling
- Added cleanup failure observability:
  - `CleanupTaskRecord.last_error`
  - redacted `last_error` stored on retrying/finally failed cleanup tasks
  - success clears `last_error`
  - in-memory and PostgreSQL metadata adapters round-trip the field
  - migration `0005_add_cleanup_task_last_error.sql`
  - migration/PostgreSQL unit fakes updated for the new column

Why it was needed:

- Backend operations fail at independent adapter boundaries: database, queue,
  object store, provider, and cleanup jobs can each fail while other state has
  already changed.
- The processor must avoid publishing terminal metadata before the queue has
  accepted the terminal transition.
- Partial durable artifact writes must not leave orphaned objects that look
  valid on a later run.
- Provider and retry failure diagnostics must remain useful without leaking
  API keys, prompt/source text, object keys, tokens, or other sensitive data.
- Cleanup failures need durable, redacted context so operators can understand
  stuck cleanup work without inspecting logs.

Provider-switch relevance:

- Useful but not strictly required for a local CLI-only provider switch.
- The provider failure classes and transient/fatal mapping are directly useful
  for real provider adapters.
- The object-store, queue, metadata, and cleanup hardening are backend
  production concerns.

### Step 7.2.1: Add Backup, Restore, And Operator Docs

What was done:

- Added `docs/operator_guide.md` covering:
  - user creation, listing, password reset, and disablement
  - service identity creation and revocation
  - bearer token listing and revocation through the API
  - automatic retry behavior and safe recovery guidance
  - run cancellation behavior and its cooperative processor semantics
  - cleanup task responsibilities, retry behavior, and current scheduler
    integration gap
  - PostgreSQL, object-store, and Redis/Valkey backup responsibilities
  - restore order and post-restore checks
  - local and optional live smoke tests
  - real-provider cost monitoring guidance
- Linked the operator guide from `docs/local_api_workflow.md`.
- Linked the operator guide from `README.md`.
- No behavior tests were added because the step explicitly marks tests as not
  applicable for behavior and the user clarified not to add unnecessary tests
  for such steps.

Why it was needed:

- Stage 7 hardening added the mechanics for redaction, cleanup, retry safety,
  and failure observability; operators also need concrete procedures for using
  those mechanics safely.
- Backup and restore must be documented before production adapter verification
  because the next stage touches real or staging AWS resources.
- The guide distinguishes implemented CLI/API paths from known gaps, such as
  the cleanup scheduler not yet having a dedicated CLI/Make target.

Provider-switch relevance:

- Not required for local CLI provider switching.
- Useful when enabling real-provider mode because it documents token handling,
  smoke tests, retry/cost behavior, and provider-spend monitoring.

## Remaining Stage 8: AWS Production Adapter Verification

Purpose:

Verify production AWS service behavior after the local provider-neutral backend
exists.

Remaining phases:

- verify PostgreSQL metadata store against managed RDS
- verify Redis or chosen queue adapter behavior
- document remaining AWS networking and secrets requirements
- run controlled fake-provider and real-provider smoke checks

Why needed:

- Production AWS services can differ from local MinIO/PostgreSQL/Redis behavior.
- The local ports make this verification lower risk.

Needed for provider switch?

- No.

### Step 8.1.1: Verify S3 ObjectStore Adapter Against AWS S3

What was done (strict TDD for behavior/config changes):

- Added AWS/S3-specific unit coverage in `tests/unit/test_s3_object_store.py`:
  - server-side encryption headers are applied to object writes, presigned
    upload URLs, and multipart upload creation
  - KMS key IDs are rejected unless `server_side_encryption='aws:kms'`
  - boto3 client construction omits static credentials when using IAM role auth
  - partial static credentials are rejected
- Added settings coverage in `tests/unit/test_config.py`:
  - `OBJECT_STORE_SERVER_SIDE_ENCRYPTION=aws:kms`
  - `OBJECT_STORE_SSE_KMS_KEY_ID`
  - validation that a KMS key requires the KMS encryption algorithm
- Added a guarded AWS S3 smoke test:
  - `tests/integration_safe/test_s3_object_store_aws.py`
  - runs only when `RUN_S3_OBJECT_STORE_SMOKE=1`
  - writes, reads, heads, signs, lists, and deletes a random
    `smoke/aws/<uuid>/` object
- Added `make test-integration` so the plan's verification command is wired.
- Confirmed the new tests failed before implementation:
  - initial targeted run: `7 failed, 32 passed, 1 skipped`
  - failures covered missing SSE constructor args, missing settings, boto3
    static-credential behavior, and missing Makefile target.
- Updated `src/urdu_pipeline/infrastructure/s3.py`:
  - added optional adapter-local `server_side_encryption` and
    `sse_kms_key_id` constructor arguments
  - applies encryption kwargs to `put_object`, presigned `put_object` params,
    and multipart upload creation
  - validates supported algorithms (`AES256`, `aws:kms`)
  - omits `None` boto3 client kwargs so IAM role/default credential resolution
    is clean
  - rejects partial static credentials
- Updated runtime wiring:
  - `src/urdu_pipeline/config/settings.py` defines and validates the new S3
    encryption settings
  - `src/urdu_pipeline/api/runtime.py` passes encryption settings into
    `S3ObjectStore` and omits empty boto3 client kwargs
  - `src/urdu_pipeline/processor/runtime.py` passes encryption settings into
    `S3ObjectStore`
- Added `docs/aws_s3_object_store.md` documenting:
  - AWS smoke-test environment variables
  - IAM role vs static credential guidance
  - SSE-S3 and SSE-KMS configuration
  - required S3 and KMS IAM permissions
  - smoke test cleanup guidance
- Linked the S3 guide from `docs/operator_guide.md`.

Verification notes:

- Targeted S3/config/Makefile suite now passes: `39 passed, 1 skipped`.
- `RUN_S3_OBJECT_STORE_SMOKE=1 make test-integration` passes locally with
  `25 passed, 4 skipped`; the AWS S3 smoke skipped because `boto3` is not
  installed in the current environment and no staging AWS bucket/credentials
  are configured.
- Full local unit plus safe integration suite passes: `854 passed, 4 skipped`.
- A real AWS staging smoke still needs to be run in an environment with
  `boto3`, `RUN_S3_OBJECT_STORE_SMOKE=1`, `AWS_S3_OBJECT_STORE_BUCKET`, region,
  and IAM role or static credentials configured.

Why it was needed:

- AWS S3 production behavior differs from MinIO local parity around credential
  sourcing and encryption headers.
- Production deployments should prefer IAM role/default credential resolution
  over static keys where possible.
- If the deployment requires bucket-default or request-level encryption, both
  direct object writes and presigned upload URLs must carry compatible headers.

Provider-switch relevance:

- Not required for local CLI provider switching.
- Required for the backend/API track before storing user uploads and durable
  artifacts in AWS S3.

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
   - Stage 4 Step 4.1.2 if continuing backend API
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
- `src/urdu_pipeline/infrastructure/secrets.py`        (NEW — EnvSecretProvider)

Admin / seed:

- `src/urdu_pipeline/admin/__init__.py`                (NEW)
- `src/urdu_pipeline/admin/seed.py`                    (NEW — seed_user, seed_service_identity,
                                                          seed_provider_config, seed_bucket)

API:

- `src/urdu_pipeline/api/__init__.py`
- `src/urdu_pipeline/api/app.py`                       (NEW — create_app factory)
- `src/urdu_pipeline/api/dependencies.py`              (NEW — AppState, get_* Depends functions)
- `src/urdu_pipeline/api/routes/__init__.py`           (NEW)
- `src/urdu_pipeline/api/routes/health.py`             (NEW — GET /health)
- `src/urdu_pipeline/api/schemas.py`                   (NEW — 21 strict public schemas)
- `src/urdu_pipeline/admin/users.py`                   (NEW — admin_create_user, admin_reset_password,
                                                          admin_disable_user, admin_list_users,
                                                          admin_revoke_service_identity)

Provider/stage safety:

- `src/urdu_pipeline/providers/requests.py`
- `src/urdu_pipeline/providers/base.py`
- `src/urdu_pipeline/providers/fake_provider.py`
- `src/urdu_pipeline/providers/openai_audio.py`
- `src/urdu_pipeline/providers/openai_text.py`
- `src/urdu_pipeline/stages/*`
- `src/urdu_pipeline/standalone/english_am_chunk_transcriber.py`

CLI/config/local stack:

- `src/urdu_pipeline/cli.py`                           (added seed-* commands)
- `src/urdu_pipeline/config/settings.py`               (added object_store_* and redis_url fields)
- `pyproject.toml`                                     (added fastapi, uvicorn, httpx2 to api/dev)
- `Makefile`
- `docker-compose.yml`
- `.env.example`                                       (added object store + redis fields)
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
- PostgreSQL metadata tests (including cross-user cache isolation test)
- S3 object-store tests
- Redis job-queue tests
- safe integration tests
- `tests/unit/test_env_secret_provider.py`             (NEW — EnvSecretProvider, SecretValue redaction)
- `tests/unit/test_seed_commands.py`                   (NEW — seed_user, seed_service_identity,
                                                          seed_provider_config, seed_bucket)
- `tests/unit/test_api_skeleton.py`                    (NEW — /health route, AppState wiring,
                                                          no secrets leak, 404 on unknown path)
- `tests/unit/test_admin_users.py`                     (NEW — 18 tests: admin_create_user,
                                                          admin_reset_password, admin_disable_user,
                                                          admin_list_users,
                                                          admin_revoke_service_identity)
- `tests/unit/test_api_schemas.py`                     (NEW — 121 tests: extra=forbid on all schemas,
                                                          forbidden field names absent, specific
                                                          forbidden fields rejected by name, valid
                                                          data round-trips)

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

- Current state: all local unit and safe integration tests pass
  (`854 passed, 4 skipped`).
- Stage 4 COMPLETE. Stage 5 COMPLETE. Stage 6 COMPLETE through Step 6.2.3.
- **Deployment target: AWS Lightsail** (decided 2026-06-07). Cloudflare is no
  longer the target. The architecture remains cloud-agnostic; only Stage 8
  content and Stage 9 provisioning changed in the plan. No code changes needed
  — all completed work was already cloud-agnostic.
- The goal is continuing the backend API conversion (Track B below).
- Stage 7 Step 7.1.1 is complete.
- Stage 7 Step 7.1.2 is complete.
- Stage 7 Step 7.1.3 is complete.
- Stage 7 Step 7.2.1 is complete.
- Stage 8 Step 8.1.1 is complete.
- Next step is Stage 8 Step 8.1.2: Verify PostgreSQL Metadata Store Against
  Managed RDS.
- IMPORTANT: For behavior/code changes, write tests BEFORE implementation
  (strict TDD), run them to confirm they fail, then implement to make them
  pass. For docs-only steps that explicitly say tests are not applicable, do
  not add unnecessary tests.
- Preserve all prompt-safety and provider-request boundaries.
- Run targeted tests before full suites.
- Do not revert unrelated work.
- Use `.venv/bin/python -m pytest` (not bare `pytest`) to run tests.
- Docker build/start smoke for Stage 6.1 still needs to be rerun once the local
  Docker/Colima daemon is started.
- Real `make compose-test` for Step 6.2.3 needs to be rerun once the local
  Docker/Colima daemon is started; the target is now wired to the fake-provider
  E2E smoke.

## Suggested Next Decision

Choose one of these tracks:

Track A: switch AI provider now

- Pause cloud/API plan.
- Implement provider adapter(s).
- Keep Stage 2 prompt-safety tests passing.
- Avoid spending time on DB/API/Redis/S3 unless needed by the provider switch.

Track B: continue backend conversion (currently active)

- **Stage 5 is fully COMPLETE** (Phases 5.1, 5.2, 5.3 all done).
  Steps 5.1.1, 5.1.2, 5.2.1–5.2.4, 5.3.1, 5.3.2 all verified.
- **Stage 6 Step 6.1.1 is COMPLETE** (API Dockerfile, processor Dockerfile,
  `.dockerignore`, and packaging tests added).
- **Stage 6 Step 6.1.2 is COMPLETE** (build-based compose services, health
  checks, env wiring, optional Nginx proxy profile, and compose config tests).
- **Stage 6 Step 6.2.1 is COMPLETE** (local API/processor dev targets, compose
  up/down/test, setup targets for migrations, bucket, user, service identity,
  and provider config).
- **Stage 6 Step 6.2.2 is COMPLETE** (local API workflow documentation and
  documentation coverage tests).
- **Stage 6 Step 6.2.3 is COMPLETE** (runtime API adapter wiring, real
  processor loop, durable artifact repository, and compose fake-provider E2E
  smoke target).
- **Deployment target changed to AWS Lightsail** (2026-06-07).
  - Stage 8 has been rewritten as "AWS Production Adapter Verification"
    (S3, RDS, Redis/SQS, Secrets Manager adapters).
  - Stage 9 provisioning updated for AWS resources.
  - Stage 8 (Cloudflare spike) is fully replaced — no work to carry forward.
  - Nothing in Stages 1–5 needs to change.
- **Stage 7 Step 7.1.1 is COMPLETE** (recursive structured logging
  redaction, API request logging, processor event sanitization, and PostgreSQL
  stage-event sanitization).
- **Stage 7 Step 7.1.2 is COMPLETE** (cleanup scheduler, deterministic cleanup
  tasks, retry handling, expired upload/session cleanup, revoked token purge,
  multipart abort, and terminal-run tmp object cleanup).
- **Stage 7 Step 7.1.3 is COMPLETE** (failure-mode tests, safer lifecycle
  transitions, provider transient/fatal failure classification, partial
  artifact cleanup, redacted retry/dead-letter reasons, and cleanup
  `last_error` observability).
- **Stage 7 Step 7.2.1 is COMPLETE** (operator guide covering users, token
  revocation, retry, cancellation, cleanup, backups, restore, smoke tests, and
  cost monitoring).
- **Stage 8 Step 8.1.1 is COMPLETE** (AWS S3 smoke test gate, IAM-role
  credential behavior, optional SSE/KMS support, `make test-integration`, and
  AWS S3 IAM/smoke documentation).
- Next step: Stage 8 Step 8.1.2 — Verify PostgreSQL Metadata Store Against
  Managed RDS.
- Then continue AWS adapter verification (Stage 8), production deploy (Stage 9).

Track C: stabilize and commit current work

- Review the current diff (all untracked new files).
- Commit the session 2 work (Steps 3.3.3, 3.3.4, 4.1.1).
- Then decide Track A or Track B.
