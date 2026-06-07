# Cloud-Agnostic API Conversion Progress Handoff

Last updated: 2026-06-07 (session 5)

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
  the plan from Step 4.1.2.

## Current Implementation Status

Completed through:

- Stage 0
- Stage 1
- Stage 2
- Stage 3 (all steps — 3.1.1 through 3.3.4 complete)
- Stage 4 (complete) + Stage 5 through Step 5.2.3

Next step in the original plan:

- Step 5.2.4: Run Translation And Article Generation

Most recent verification:

- Combined unit + safe integration: `753 passed, 3 skipped`
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

## What Is Left In The Original Plan

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

## Remaining Stage 4 Work

Why needed:

- Required for a secure hosted API.

Needed for provider switch?

- No.

## Remaining Stage 4: Auth And API Backend (routes)

Purpose:

Build the public backend API resource routes.

Remaining phases:

- upload/run/artifact/event routes (Steps 4.3.1–4.3.4)
- OpenAPI generation/review (Step 4.3.5)

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

- Current state: all unit and safe integration tests pass (753 passed, 3 skipped).
- Stage 4 COMPLETE. Stage 5 in progress (Steps 5.1.1, 5.1.2, 5.2.1, 5.2.2, and 5.2.3 done).
- **Deployment target: AWS Lightsail** (decided 2026-06-07). Cloudflare is no
  longer the target. The architecture remains cloud-agnostic; only Stage 8
  content and Stage 9 provisioning changed in the plan. No code changes needed
  — all completed work was already cloud-agnostic.
- The goal is continuing the backend API conversion (Track B below).
- Next step is Step 5.2.4: Run Translation And Article Generation.
- IMPORTANT: Always write tests BEFORE implementation (strict TDD). Run them to
  confirm they fail, then implement to make them pass.
- Preserve all prompt-safety and provider-request boundaries.
- Run targeted tests before full suites.
- Do not revert unrelated work.
- Use `.venv/bin/python -m pytest` (not bare `pytest`) to run tests.

## Suggested Next Decision

Choose one of these tracks:

Track A: switch AI provider now

- Pause cloud/API plan.
- Implement provider adapter(s).
- Keep Stage 2 prompt-safety tests passing.
- Avoid spending time on DB/API/Redis/S3 unless needed by the provider switch.

Track B: continue backend conversion (currently active)

- Stage 4 is COMPLETE. Stage 5 Phase 5.1 is COMPLETE (5.1.1 + 5.1.2 done).
  Steps 5.2.1, 5.2.2, and 5.2.3 are also COMPLETE.
- **Deployment target changed to AWS Lightsail** (2026-06-07).
  - Stage 8 has been rewritten as "AWS Production Adapter Verification"
    (S3, RDS, Redis/SQS, Secrets Manager adapters).
  - Stage 9 provisioning updated for AWS resources.
  - Stage 8 (Cloudflare spike) is fully replaced — no work to carry forward.
  - Nothing in Stages 1–5.2.3 needs to change.
- Next step: Step 5.2.4 — Run Translation And Article Generation.
- Then pipeline integration (5.2.2–5.2.4), failure handling (5.3.x), local
  parity stack (Stage 6), hardening (Stage 7), AWS adapter verification
  (Stage 8), production deploy (Stage 9).

Track C: stabilize and commit current work

- Review the current diff (all untracked new files).
- Commit the session 2 work (Steps 3.3.3, 3.3.4, 4.1.1).
- Then decide Track A or Track B.
