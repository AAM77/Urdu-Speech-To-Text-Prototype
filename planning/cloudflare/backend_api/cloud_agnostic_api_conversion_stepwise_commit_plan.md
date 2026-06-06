# Cloud-Agnostic API Conversion Stepwise Commit Plan

Updated: 2026-06-06

## Purpose

Convert the existing Urdu speech-to-text CLI/Streamlit prototype into a secure
API-backed application that can be developed and tested locally in a
production-like environment. Cloudflare Free is the first likely cloud target,
but Cloudflare must remain an adapter, not the core application architecture.

This plan contains the same architectural direction as
`cloud_agnostic_api_conversion_plan.md`, reorganized into numbered stages,
phases, and small commit-sized steps.

## Non-Negotiable Instructions For The Implementing AI

1. Implement exactly one numbered step at a time.
2. Stop after every numbered step.
3. Do not continue to the next step until the user reviews the work and gives
   explicit approval.
4. Treat every numbered step as one reviewable commit candidate.
5. Use TDD whenever code behavior is being added or changed:
   - Write the smallest meaningful failing tests first.
   - Confirm the tests fail for the expected reason.
   - Implement only enough code to pass those tests.
   - Run the relevant test subset.
   - Then run broader tests when the step touches shared behavior.
6. If a step is documentation-only or scaffolding-only, still perform a
   reviewable verification such as import checks, lint checks if configured,
   `git diff --check`, or targeted smoke commands.
7. Keep each step small. Do not bundle unrelated refactors, dependency churn,
   cloud adapters, route implementation, and processor behavior in one step.
8. Do not expose any public endpoint that accepts prompts, prompt IDs, model
   IDs, provider IDs, raw transcript text, raw translation text, raw article
   text, artifact JSON, object keys, or user IDs for ownership decisions.
9. Keep the existing CLI, fake providers, artifact schemas, cache tests,
   cost/budget tests, and safe integration tests working throughout the
   migration.
10. Never move Cloudflare, AWS, GCP, Azure, MinIO, Redis, or provider SDK logic
    into core domain/application modules. They belong in adapters.

Every step must end with this message shape:

```text
Step completed: <stage.phase.step name>
Tests/verification run: <commands>
Result: <pass/fail and important details>
Files changed: <short list>
STOPPING for user review before the next step.
```

## Codebase Facts To Preserve

- CLI entry point: `src/urdu_pipeline/cli.py`.
- Streamlit prototype UI: `src/urdu_pipeline/ui/streamlit_app.py`.
- Current stages:
  - `src/urdu_pipeline/stages/chunker.py`
  - `src/urdu_pipeline/stages/transcriber.py`
  - `src/urdu_pipeline/stages/transcript_reconciler.py`
  - `src/urdu_pipeline/stages/translator.py`
  - `src/urdu_pipeline/stages/article_generator.py`
  - `src/urdu_pipeline/standalone/english_am_chunk_transcriber.py`
- Current providers:
  - `src/urdu_pipeline/providers/base.py`
  - `src/urdu_pipeline/providers/fake_provider.py`
  - `src/urdu_pipeline/providers/openai_audio.py`
  - `src/urdu_pipeline/providers/openai_text.py`
- Artifact schemas already use strict Pydantic validation.
- Artifact validators load by `artifact_type`.
- Fake providers support deterministic no-network tests.
- `safe_log_event` already avoids obvious secret/body fields and should be
  extended, not discarded.
- Current stages are filesystem-first through `ArtifactStore`; the API
  migration must separate local scratch workspace from durable artifact storage.
- Current translator/article code embeds untrusted source text into prompt
  strings; this must be refactored before public API exposure.
- Current CLI artifact JSON resume behavior is local-only and must not become a
  public API behavior.

## Target Architecture Summary

Canonical local and robust first deployment:

```text
Frontend/API client
  -> FastAPI API service on CPython
  -> PostgreSQL metadata database
  -> S3-compatible object store
  -> Redis/Valkey or compatible queue adapter
  -> Python processor container with ffmpeg/ffprobe
  -> Provider registry + secret provider + model provider clients
```

Cloudflare-first experiment:

```text
Cloudflare Worker or Pages Function as thin ingress only
  -> same API contract or proxy to FastAPI
  -> R2 object storage adapter
  -> external PostgreSQL first, D1 only after contract-test acceptance
  -> queue adapter
  -> external Python processor
```

Cloudflare constraints:

- Do not run the full pipeline, `ffmpeg`, or long-running processor inside
  Workers.
- Use signed object-storage upload URLs because edge request-body limits matter.
- Treat D1 as a possible adapter only after PostgreSQL/local contract tests are
  stable and D1 limits are accepted.
- Re-check current Cloudflare Workers, Python Workers, D1, R2, and Queues
  limits immediately before any Cloudflare deployment work.

## Core Security Rules

- Preconfigured users only; no public signup.
- Passwords, bearer tokens, and service tokens are hashed at rest.
- Browser sessions use HTTP-only cookies, secure cookies in production, and
  CSRF protection for cookie-authenticated writes.
- Bearer tokens are generated server-side, shown once, revocable, and used for
  curl/Postman.
- Processor/service identities are separate from user sessions and user bearer
  tokens.
- Every upload, run, job, artifact, event, usage record, and cache entry is
  user-owned or explicitly scoped.
- Public API responses do not expose raw object keys.
- Object keys use opaque IDs only. Original and sanitized filenames are
  metadata and optional download display names, not object-key components.
- Queue payloads contain only `job_id` plus safe routing metadata.
- The persisted job table is authoritative for state, leases, retries,
  cancellation, and terminal outcomes.
- Provider/model/prompt selection is server-controlled through a versioned
  provider registry.
- Treat transcript, translation, article, and previous chunk context as
  untrusted model input.
- Do not log prompts, secrets, raw transcript text, full translations, article
  bodies, full artifacts, or object keys.

## Provider-Neutral Object Key Layout

Use this shape unless a later reviewed step explicitly changes it:

```text
tmp/users/{user_id}/uploads/{upload_id}/source
tmp/users/{user_id}/runs/{run_id}/input/source
tmp/users/{user_id}/runs/{run_id}/chunks/{chunk_id}.{audio_ext}
artifacts/users/{user_id}/runs/{run_id}/{stage}/{artifact_id}/artifact.json
artifacts/users/{user_id}/runs/{run_id}/{stage}/{artifact_id}/artifact.md
cache/users/{user_id}/{scope}/{cache_key}.json
cache/shared/{shared_scope_name}/{cache_key}.json
```

Shared cache entries are prohibited in V1 unless a separate security review
approves exact scope and leakage risk.

## Stage 0: Decisions, Dependency Split, And Local Skeleton

Stage goal: lock the architecture choices and prepare the repository for
separate CLI, API, processor, adapter, and local-stack work without changing
pipeline behavior.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 0.1: Architecture Decisions

Phase goal: create durable architecture records so later implementation choices
do not drift into Cloudflare coupling.

Phase stop rule: stop after every numbered step in this phase.

#### Step 0.1.1: Add ADR For Canonical Runtime Shape

Tests first: not applicable for behavior. Before editing, identify whether an
ADR/docs convention already exists.

Implementation:

- Add an ADR stating that FastAPI on CPython is the canonical API runtime.
- State that the Python processor is separate from the API.
- State that PostgreSQL is the canonical local parity metadata database.
- State that S3-compatible object storage is the object-store port target.
- State that Cloudflare is an adapter/deployment option, not the business logic
  source of truth.

Verification:

- Run `git diff --check`.
- Confirm the ADR references the existing plan and this stepwise plan.
- Stop for review and commit.

#### Step 0.1.2: Add ADR For Processor Trust Boundary

Tests first: not applicable for behavior.

Implementation:

- Add an ADR stating that queue messages contain only `job_id` plus safe
  routing metadata.
- State that the persisted job table is authoritative.
- State that the processor authenticates as a service identity separate from
  users.
- State that processor access is least-privilege and cannot mint users/tokens
  or bypass public API ownership checks.

Verification:

- Run `git diff --check`.
- Stop for review and commit.

#### Step 0.1.3: Add ADR For Opaque Object Keys

Tests first: not applicable for behavior.

Implementation:

- Add an ADR requiring opaque object keys.
- Explicitly prohibit original or sanitized filenames in object keys.
- State that filenames are metadata and optional signed-download display names.

Verification:

- Run `git diff --check`.
- Stop for review and commit.

### Phase 0.2: Dependency And Module Skeleton

Phase goal: split dependency concerns and create module boundaries without
changing runtime behavior.

Phase stop rule: stop after every numbered step in this phase.

#### Step 0.2.1: Split Optional Dependencies

Tests first:

- Add or update tests/import checks proving importing core package modules does
  not require Streamlit, FastAPI, processor dependencies, or provider SDKs unless
  those extras are installed, if such checks are practical in the existing test
  setup.

Implementation:

- Split `pyproject.toml` optional dependencies into `core`, `cli`, `ui`, `api`,
  `processor`, and `dev`.
- Keep existing install/test behavior working.
- Avoid pin churn unrelated to the split.

Verification:

- Run targeted import tests.
- Run existing unit tests if dependency changes affect imports.
- Stop for review and commit.

#### Step 0.2.2: Add No-Op Module Skeleton

Tests first:

- Add import tests for the new empty modules if practical.

Implementation:

- Add empty or minimal modules:
  - `src/urdu_pipeline/domain/`
  - `src/urdu_pipeline/application/`
  - `src/urdu_pipeline/infrastructure/`
  - `src/urdu_pipeline/api/`
  - `src/urdu_pipeline/processor/`
- Do not move existing logic yet.

Verification:

- Run targeted import tests.
- Stop for review and commit.

### Phase 0.3: Local Stack Skeleton

Phase goal: introduce local parity structure early, before API and processor
behavior depends on mocks only.

Phase stop rule: stop after every numbered step in this phase.

#### Step 0.3.1: Add Docker Compose Skeleton

Tests first: not applicable for behavior.

Implementation:

- Add `docker-compose.yml` with placeholder or real services for:
  - API
  - processor
  - PostgreSQL
  - MinIO
  - Redis/Valkey
  - optional reverse proxy
- Add `.env.local.example`.
- Add health-check placeholders.

Verification:

- Run `docker compose config` if Docker is available.
- Run `git diff --check`.
- Stop for review and commit.

#### Step 0.3.2: Add Make Targets For Local Stack

Tests first: not applicable unless Makefile tests exist for target presence.

Implementation:

- Add `make compose-up` and `make compose-test` placeholders that fail clearly
  if later implementation is missing.
- Do not implement the full workflow yet.

Verification:

- Run Makefile tests or targeted make target smoke checks.
- Stop for review and commit.

## Stage 1: Core Domain, Ports, And Contract Tests

Stage goal: define cloud-neutral contracts before writing API, processor, or
cloud-provider-specific code.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 1.1: Domain IDs And States

Phase goal: define stable domain concepts used by all adapters and services.

Phase stop rule: stop after every numbered step in this phase.

#### Step 1.1.1: Add Domain ID Types And Builders

Tests first:

- Add tests for ID creation, validation, serialization, and rejection of unsafe
  values.

Implementation:

- Add domain ID types/builders for users, uploads, runs, jobs, artifacts,
  provider config versions, provider runs, service identities, and cleanup
  tasks.
- Prefer UUID/ULID style opaque IDs.

Verification:

- Run the new ID tests.
- Stop for review and commit.

#### Step 1.1.2: Add State Enums

Tests first:

- Add tests proving allowed states and state string values are stable.

Implementation:

- Add enums for upload status, run status, job status, job attempt status,
  artifact stage/type where useful, provider config status, cleanup task
  status, and user/service identity status.

Verification:

- Run enum/state tests.
- Stop for review and commit.

### Phase 1.2: Ports

Phase goal: define interfaces for application code without importing concrete
cloud SDKs.

Phase stop rule: stop after every numbered step in this phase.

#### Step 1.2.1: Add Storage And Workspace Ports

Tests first:

- Add protocol/import tests or type-oriented tests where practical.

Implementation:

- Add `ObjectStore` with streaming, signed URL, metadata, delete/list prefix,
  and multipart lifecycle methods.
- Add `RunWorkspace` for local scratch files.
- Add `ArtifactRepository` for durable artifact storage.
- Add stage-facing `ArtifactSink` for stage refactor compatibility.

Verification:

- Run targeted tests/import checks.
- Stop for review and commit.

#### Step 1.2.2: Add Metadata, Queue, Cache, Auth, Secrets, Provider, And Usage Ports

Tests first:

- Add protocol/import tests or type-oriented tests where practical.

Implementation:

- Add `MetadataStore`, `JobQueue`, `CacheStore`, `AuthService`,
  `SecretProvider`, `ProviderRegistry`, `UsageLedger`, and `BudgetService`
  ports.
- Include queue methods for enqueue, claim, extend lease, retry, terminal
  failure, cancellation, and dead-letter.

Verification:

- Run targeted tests/import checks.
- Stop for review and commit.

### Phase 1.3: Object Keys And In-Memory Adapters

Phase goal: implement safe local test doubles and object-key rules.

Phase stop rule: stop after every numbered step in this phase.

#### Step 1.3.1: Add Opaque Object-Key Builder

Tests first:

- Add tests proving object keys include user/run/upload/artifact scope.
- Add tests proving raw filenames and sanitized filenames never appear in keys.
- Add traversal and weird filename fixtures.

Implementation:

- Implement the provider-neutral object-key builder.
- Keep display filename handling separate from object-key construction.

Verification:

- Run object-key tests.
- Stop for review and commit.

#### Step 1.3.2: Add In-Memory Object And Metadata Adapters

Tests first:

- Add contract tests for basic object put/get/head/delete/list behavior.
- Add metadata ownership tests for users, uploads, runs, jobs, and artifacts.

Implementation:

- Implement in-memory `ObjectStore`.
- Implement in-memory `MetadataStore`.
- Enforce ownership in adapter methods.

Verification:

- Run adapter contract tests.
- Stop for review and commit.

#### Step 1.3.3: Add In-Memory Queue, Cache, Secrets, Provider Registry, And Usage Adapters

Tests first:

- Add contract tests for enqueue/claim/lease/retry/cancel/dead-letter.
- Add tests proving queue payloads contain only `job_id` and safe metadata.
- Add cache-scope tests proving no cross-user leakage.
- Add missing-secret tests proving fail-closed behavior.

Implementation:

- Implement in-memory `JobQueue`.
- Implement in-memory `CacheStore`.
- Implement fake/in-memory `SecretProvider`.
- Implement in-memory `ProviderRegistry`.
- Implement in-memory `UsageLedger`/`BudgetService`.

Verification:

- Run adapter contract tests.
- Stop for review and commit.

## Stage 2: Stage Boundary And Prompt-Safety Refactor

Stage goal: make existing stages usable by both CLI and processor while
preserving local CLI behavior and improving prompt safety before API exposure.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 2.1: Filesystem Compatibility

Phase goal: wrap existing filesystem-first behavior without changing outputs.

Phase stop rule: stop after every numbered step in this phase.

#### Step 2.1.1: Add Filesystem Workspace And Artifact Sink Adapters

Tests first:

- Add tests proving the adapters preserve the existing run directory layout.
- Add tests for path traversal rejection in workspace path resolution.

Implementation:

- Add `FilesystemRunWorkspace` backed by current run directories.
- Add `FilesystemArtifactSink` delegating to existing `ArtifactStore`.
- Do not refactor stage constructors yet.

Verification:

- Run new filesystem adapter tests and existing artifact tests.
- Stop for review and commit.

#### Step 2.1.2: Add Filesystem Cache Adapter

Tests first:

- Add tests proving cache lookup/store behavior matches `ArtifactCache`.
- Add tests for corrupt-entry-as-miss behavior.
- Add user-scope tests if API cache scope is introduced here.

Implementation:

- Add `FilesystemCacheStore` wrapping or adapting `ArtifactCache`.
- Preserve current CLI cache behavior.

Verification:

- Run cache tests.
- Stop for review and commit.

### Phase 2.2: Provider Request Objects And Prompt Tests

Phase goal: separate trusted instructions from untrusted source data.

Phase stop rule: stop after every numbered step in this phase.

#### Step 2.2.1: Add Provider Request Models

Tests first:

- Add tests for request construction and serialization/redaction.
- Add tests proving source text is separate from instruction text.

Implementation:

- Replace primitive provider call shapes with request objects, while preserving
  backwards-compatible adapters where needed.
- Include fields for system/developer instructions, source data, schema
  instructions, model parameters, prompt metadata, and redaction-safe checksums.

Verification:

- Run provider request tests.
- Stop for review and commit.

#### Step 2.2.2: Add Prompt-Injection Fixtures

Tests first:

- Add fixtures where transcript text, translation text, article source text, and
  previous chunk tail contain instructions such as "ignore previous
  instructions".
- These tests should fail until prompt construction is refactored.

Implementation:

- Only add tests/fixtures in this step unless tiny helper changes are required.

Verification:

- Run the new tests and confirm expected failures or mark them as expected
  failing only if that is the project convention.
- Stop for review and commit.

### Phase 2.3: Refactor Stages One At A Time

Phase goal: migrate stages to the new boundaries in small reviewable units.

Phase stop rule: stop after every numbered step in this phase.

#### Step 2.3.1: Refactor Chunker Stage Boundary

Tests first:

- Add or update tests proving chunker writes chunks to workspace and persists
  artifacts through `ArtifactSink`.

Implementation:

- Refactor chunker to use `RunWorkspace` and `ArtifactSink`.
- Keep CLI compatibility wrapper.

Verification:

- Run chunker tests and safe integration tests relevant to chunking.
- Stop for review and commit.

#### Step 2.3.2: Refactor Urdu Transcriber Stage Boundary

Tests first:

- Add tests proving chunk paths resolve through `RunWorkspace`.
- Add tests proving usage can be recorded through `UsageLedger`.
- Add adversarial previous-chunk-tail prompt tests.

Implementation:

- Refactor Urdu transcriber to use workspace, sink, cache, provider request
  object, and usage ledger.
- Keep CLI compatibility wrapper.

Verification:

- Run transcriber tests and fake-provider tests.
- Stop for review and commit.

#### Step 2.3.3: Refactor English AM Transcriber Stage Boundary

Tests first:

- Mirror the Urdu transcriber tests for English AM behavior.

Implementation:

- Refactor English AM transcriber to use the same boundaries.
- Keep CLI compatibility wrapper.

Verification:

- Run English AM transcriber tests.
- Stop for review and commit.

#### Step 2.3.4: Refactor Reconciler Stage Boundary

Tests first:

- Add tests proving deterministic reconciliation still produces the same
  artifact shape and persists through `ArtifactSink`.

Implementation:

- Refactor reconciler to use `ArtifactSink`.
- Keep deterministic behavior.

Verification:

- Run reconciler tests.
- Stop for review and commit.

#### Step 2.3.5: Refactor Translator Prompt And Stage Boundary

Tests first:

- Add/update tests proving trusted instructions and Urdu source text are
  separate where provider supports structured inputs.
- Add fallback prompt tests proving source text is fenced and described as data.

Implementation:

- Refactor translator to use provider request object, `ArtifactSink`,
  `CacheStore`, provider config, and usage ledger.

Verification:

- Run translator tests and prompt-injection tests.
- Stop for review and commit.

#### Step 2.3.6: Refactor Article Generator Prompt And Stage Boundary

Tests first:

- Add/update tests proving source translation text is separate or fenced.
- Add structured-output/schema tests where provider supports it.

Implementation:

- Refactor article generator to use provider request object, `ArtifactSink`,
  `CacheStore`, provider config, and usage ledger.

Verification:

- Run article generator tests and prompt-injection tests.
- Stop for review and commit.

#### Step 2.3.7: Run Full CLI Compatibility Pass

Tests first: existing tests are the test source for this step.

Implementation:

- Make only compatibility fixes required by the previous refactors.
- Do not add API, processor, or persistence adapters here.

Verification:

- Run existing unit tests.
- Run safe integration tests.
- Run Makefile orchestration tests.
- Stop for review and commit.

## Stage 3: Local Persistence Adapters

Stage goal: make the local stack production-like before building the public API
workflow around it.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 3.1: Database Schema And Migrations

Phase goal: create durable relational state with ownership, leases, config
snapshots, and usage ledgers.

Phase stop rule: stop after every numbered step in this phase.

#### Step 3.1.1: Add Migration Framework

Tests first:

- Add tests or smoke checks proving migrations can run against an empty local
  PostgreSQL database.

Implementation:

- Add a migration tool and migration command.
- Wire it into local config.

Verification:

- Run migration smoke check against PostgreSQL if available.
- Stop for review and commit.

#### Step 3.1.2: Add Core User/Auth/Upload/Run Tables

Tests first:

- Add migration tests for table creation, required indexes, and basic
  constraints.

Implementation:

- Add `users`, `sessions`, `api_tokens`, `service_identities`, `uploads`, and
  `runs` tables.

Verification:

- Run migration tests.
- Stop for review and commit.

#### Step 3.1.3: Add Job, Attempt, Artifact, Event, Usage, Cache, Provider Config, Prompt, And Cleanup Tables

Tests first:

- Add migration tests for table creation, indexes, uniqueness constraints, and
  ownership columns.

Implementation:

- Add `jobs`, `job_attempts`, `artifacts`, `artifact_document_chunks`,
  `stage_events`, `provider_runs`, `usage_ledger`, `cache_entries`,
  `provider_config_versions`, `provider_config_entries`, `prompt_versions`, and
  `cleanup_tasks`.

Verification:

- Run migration tests.
- Stop for review and commit.

### Phase 3.2: PostgreSQL Metadata Store

Phase goal: implement durable metadata behavior behind the `MetadataStore`
contract.

Phase stop rule: stop after every numbered step in this phase.

#### Step 3.2.1: Implement User/Auth/Upload/Run Metadata Methods

Tests first:

- Add contract tests for ownership checks, create/read/list behavior, and
  transaction rollback.

Implementation:

- Implement the relevant `PostgresMetadataStore` methods.
- Do not implement jobs/artifacts yet unless required by tests.

Verification:

- Run PostgreSQL metadata contract tests for this subset.
- Stop for review and commit.

#### Step 3.2.2: Implement Job Lease And State Metadata Methods

Tests first:

- Add tests for compare-and-set job claiming, lease extension, lease expiry,
  cancellation, retry, terminal failure, and dead-letter state.

Implementation:

- Implement persisted job lifecycle methods.

Verification:

- Run job metadata contract tests.
- Stop for review and commit.

#### Step 3.2.3: Implement Artifact, Event, Usage, Cache, Provider Config, Prompt, And Cleanup Metadata Methods

Tests first:

- Add tests for artifact document chunk round-trip below 256 KB.
- Add tests for provider config version snapshots.
- Add tests for usage reservations/releases/actual costs surviving restart.
- Add tests for cleanup task idempotency.

Implementation:

- Implement the remaining metadata methods.

Verification:

- Run full PostgreSQL metadata contract tests.
- Stop for review and commit.

### Phase 3.3: Object Store, Queue, Secrets, Cache, And Seeds

Phase goal: provide local services that mimic cloud behavior.

Phase stop rule: stop after every numbered step in this phase.

#### Step 3.3.1: Implement MinIO/S3 Object Store

Tests first:

- Add contract tests for put/get/head/delete, list/delete prefix, metadata,
  signed upload/download URLs, and multipart lifecycle where supported.

Implementation:

- Implement `S3ObjectStore` against MinIO/S3-compatible APIs.

Verification:

- Run object-store contract tests against MinIO.
- Stop for review and commit.

#### Step 3.3.2: Implement Redis/Valkey Job Queue

Tests first:

- Add contract tests proving Redis/Valkey delivery cannot override persisted
  job state.
- Add duplicate/stale message tests.

Implementation:

- Implement `RedisJobQueue` or a DB-backed queue adapter if explicitly chosen.
- Keep `jobs` table authoritative.

Verification:

- Run queue contract tests.
- Stop for review and commit.

#### Step 3.3.3: Implement Local Secrets And Scoped Cache Store

Tests first:

- Add missing-secret fail-closed tests.
- Add log redaction tests for secret values.
- Add cache no-cross-user-leakage tests.

Implementation:

- Implement environment-backed local secret provider.
- Implement user-scoped cache store.

Verification:

- Run secret/cache contract tests.
- Stop for review and commit.

#### Step 3.3.4: Add Seed Commands

Tests first:

- Add tests for seed user, seed service identity, and seed provider registry
  commands.

Implementation:

- Add seed/admin commands for users, service identities, provider config, and
  MinIO bucket setup.

Verification:

- Run seed command tests or local smoke checks.
- Stop for review and commit.

## Stage 4: Auth And API Backend

Stage goal: expose a strict, user-scoped API that is safe enough to run locally
against the parity stack.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 4.1: API Foundation And Schemas

Phase goal: create FastAPI app structure and strict schemas without implementing
all routes at once.

Phase stop rule: stop after every numbered step in this phase.

#### Step 4.1.1: Add FastAPI App Skeleton

Tests first:

- Add health endpoint tests.
- Add dependency wiring tests with in-memory adapters.

Implementation:

- Add FastAPI app, dependencies, route package, and health route.
- Keep internals cloud-neutral.

Verification:

- Run API skeleton tests.
- Stop for review and commit.

#### Step 4.1.2: Add Strict Public Request/Response Schemas

Tests first:

- Add tests proving unknown fields are rejected.
- Add tests proving public schemas reject `user_id`, object keys, provider/model
  fields, prompts, raw text, and artifact JSON.

Implementation:

- Add request/response schemas for auth, uploads, runs, events, artifacts, and
  tokens.

Verification:

- Run schema tests.
- Stop for review and commit.

### Phase 4.2: Auth, Sessions, Tokens, CSRF, CORS, Rate Limits

Phase goal: implement authentication and request safety before resource routes.

Phase stop rule: stop after every numbered step in this phase.

#### Step 4.2.1: Add Admin CLI For Users And Service Identities

Tests first:

- Add tests for create user, reset password, disable user, list users, create
  service identity, and revoke service identity.

Implementation:

- Implement admin CLI commands.
- No public signup endpoint.

Verification:

- Run admin CLI tests.
- Stop for review and commit.

#### Step 4.2.2: Add Login, Logout, Session Resolution

Tests first:

- Add tests for login success/failure, logout, session revocation, expiry, and
  HTTP-only cookie attributes.

Implementation:

- Implement session auth endpoints and dependencies.

Verification:

- Run auth tests.
- Stop for review and commit.

#### Step 4.2.3: Add Bearer Token Auth

Tests first:

- Add tests proving tokens are shown once, hashed at rest, revocable, expire,
  and update `last_used_at`.

Implementation:

- Implement bearer token creation/revocation and auth dependency.

Verification:

- Run bearer token tests.
- Stop for review and commit.

#### Step 4.2.4: Add CSRF, CORS, And Rate Limits

Tests first:

- Add tests proving cookie-authenticated mutating requests require CSRF.
- Add tests proving bearer-token requests do not require CSRF.
- Add tests for CORS allowlist behavior.
- Add tests for auth/upload/run-creation rate limits.

Implementation:

- Implement CSRF, CORS, and rate-limit middleware/dependencies.

Verification:

- Run security middleware tests.
- Stop for review and commit.

### Phase 4.3: Upload, Run, Artifact, And Event Routes

Phase goal: add resource routes in small groups with ownership checks.

Phase stop rule: stop after every numbered step in this phase.

#### Step 4.3.1: Add Upload Init And Complete Routes

Tests first:

- Add tests for signed upload init, ownership, allowed extension/content type,
  declared size, checksum, object metadata validation, and no object-key leakage.

Implementation:

- Implement `POST /uploads/init`, `POST /uploads/{upload_id}/complete`, and
  `GET /uploads/{upload_id}`.

Verification:

- Run upload route tests.
- Stop for review and commit.

#### Step 4.3.2: Add Multipart And Direct Upload Routes

Tests first:

- Add tests for multipart init, part URL creation, completion validation, abort,
  ownership, part-size constraints, total-size constraints, and direct upload
  policy parity.

Implementation:

- Implement multipart upload endpoints and `/uploads/direct`.

Verification:

- Run multipart/direct upload tests.
- Stop for review and commit.

#### Step 4.3.3: Add Run Routes

Tests first:

- Add tests for run creation, provider config version snapshot, queue enqueue,
  list/read ownership, cancellation ownership, invalid fields, and bounded
  numeric chunking options.

Implementation:

- Implement `POST /runs`, `GET /runs`, `GET /runs/{run_id}`,
  `GET /runs/{run_id}/events`, and `POST /runs/{run_id}/cancel`.

Verification:

- Run run route tests.
- Stop for review and commit.

#### Step 4.3.4: Add Artifact Routes

Tests first:

- Add tests for artifact list/read/download ownership, JSON/Markdown formats,
  signed download URL behavior, and no raw object-key exposure.

Implementation:

- Implement `GET /runs/{run_id}/artifacts`,
  `GET /artifacts/{artifact_id}?format=json|markdown`, and
  `GET /artifacts/{artifact_id}/download?format=json|markdown`.

Verification:

- Run artifact route tests.
- Stop for review and commit.

#### Step 4.3.5: Generate And Review OpenAPI Schema

Tests first:

- Add schema snapshot or validation tests if practical.

Implementation:

- Generate OpenAPI schema.
- Verify public schema does not include internal routes or prohibited fields.

Verification:

- Run OpenAPI validation tests.
- Stop for review and commit.

## Stage 5: Processor And Job Execution

Stage goal: process queued runs through the refactored pipeline using private
service identity, durable state, object storage, and persisted budgets.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 5.1: Processor Foundation And Job Lifecycle

Phase goal: build the processor shell and job lifecycle without running the full
pipeline yet.

Phase stop rule: stop after every numbered step in this phase.

#### Step 5.1.1: Add Processor Command And Service Auth

Tests first:

- Add tests proving service identity auth works for processor operations.
- Add tests proving service tokens cannot call normal user endpoints and user
  tokens cannot call processor/internal endpoints.

Implementation:

- Add processor command/configuration.
- Add service identity auth dependency or direct adapter wiring.

Verification:

- Run processor auth tests.
- Stop for review and commit.

#### Step 5.1.2: Implement Claim, Heartbeat, Lease, Retry, Cancel, Dead-Letter

Tests first:

- Add processor job lifecycle tests with fake adapters.
- Include duplicate queue message and stale lease cases.

Implementation:

- Implement job polling/claiming, heartbeat, lease extension, cancellation,
  retry, terminal failure, and dead-letter handling.

Verification:

- Run processor lifecycle tests.
- Stop for review and commit.

### Phase 5.2: Workspace And Pipeline Execution

Phase goal: materialize object storage into local workspace and run stages.

Phase stop rule: stop after every numbered step in this phase.

#### Step 5.2.1: Materialize Workspace And Validate Audio

Tests first:

- Add tests for downloading upload object to workspace, path safety, workspace
  cleanup, `ffprobe` validation failure, and safe error events.

Implementation:

- Implement workspace materialization from `ObjectStore`.
- Validate audio with `ffprobe` before expensive work.

Verification:

- Run workspace/audio validation tests.
- Stop for review and commit.

#### Step 5.2.2: Run Chunker And Persist Chunk Manifest

Tests first:

- Add processor tests for chunker stage, temporary chunk upload, manifest
  persistence, stage events, and cleanup behavior.

Implementation:

- Run chunker through ports.
- Upload chunks under `tmp/`.
- Save chunk manifest through `ArtifactRepository`.

Verification:

- Run processor chunker tests.
- Stop for review and commit.

#### Step 5.2.3: Run Transcription And Reconciliation

Tests first:

- Add tests for transcription chunk loop, cancellation between chunks, provider
  run metadata, usage ledger records, raw transcript artifact, and reconciled
  transcript artifact.

Implementation:

- Run transcription and reconciliation through ports.

Verification:

- Run processor transcription/reconciliation tests.
- Stop for review and commit.

#### Step 5.2.4: Run Translation And Article Generation

Tests first:

- Add tests for budget enforcement before stages, provider run records, usage
  ledger persistence, prompt-safety behavior, translation artifact, and article
  artifact.

Implementation:

- Run translation and article generation through ports.

Verification:

- Run processor translation/article tests.
- Stop for review and commit.

### Phase 5.3: Processor Failure Handling And Cleanup

Phase goal: make retries, idempotency, and cleanup safe.

Phase stop rule: stop after every numbered step in this phase.

#### Step 5.3.1: Add Idempotent Retry Behavior

Tests first:

- Add tests simulating processor crash after each stage.
- Prove durable artifacts and spend are not duplicated.

Implementation:

- Implement idempotency keys and retry-safe artifact/usage writes.

Verification:

- Run retry/idempotency tests.
- Stop for review and commit.

#### Step 5.3.2: Add Temporary Object And Workspace Cleanup

Tests first:

- Add tests for success cleanup, terminal failure cleanup, retry-preserving
  input behavior, and local workspace cleanup on exceptions.

Implementation:

- Implement cleanup of safe temporary objects and local workspace files.

Verification:

- Run cleanup tests.
- Stop for review and commit.

## Stage 6: Full Local Parity Stack

Stage goal: make local development mimic the eventual cloud topology closely
enough to trust pre-deployment tests.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 6.1: Containers And Compose

Phase goal: finish local runtime packaging.

Phase stop rule: stop after every numbered step in this phase.

#### Step 6.1.1: Add API And Processor Dockerfiles

Tests first:

- Add build smoke checks if the project uses scripted checks.

Implementation:

- Add Dockerfile(s) for API and processor.
- Ensure processor image includes `ffmpeg`/`ffprobe`.

Verification:

- Build images if Docker is available.
- Stop for review and commit.

#### Step 6.1.2: Finish Docker Compose Services

Tests first:

- Add compose config validation or smoke checks.

Implementation:

- Finish Compose wiring for API, processor, PostgreSQL, MinIO, Redis/Valkey,
  health checks, and optional reverse proxy.

Verification:

- Run `docker compose config`.
- Start services if available.
- Stop for review and commit.

### Phase 6.2: Local Commands, Docs, And E2E

Phase goal: make the local parity workflow usable and testable.

Phase stop rule: stop after every numbered step in this phase.

#### Step 6.2.1: Add Local Setup Commands

Tests first:

- Add tests or smoke checks for migration, seed user, seed provider registry,
  and bucket setup commands.

Implementation:

- Add `make api-dev`, `make processor-dev`, `make compose-up`,
  `make compose-down`, and `make compose-test`.
- Add setup commands for database, user, provider config, and bucket.

Verification:

- Run make target smoke checks.
- Stop for review and commit.

#### Step 6.2.2: Add Local Workflow Documentation

Tests first: not applicable for behavior.

Implementation:

- Document login, token creation, upload, run creation, polling, artifact read,
  artifact download, cancellation, retry, and cleanup.
- Make fake-provider mode the local default.

Verification:

- Run `git diff --check`.
- Stop for review and commit.

#### Step 6.2.3: Add Compose Fake-Provider E2E Test

Tests first:

- Add the E2E test before fixing any implementation gaps it reveals.

Implementation:

- Test seed user, login, upload fake/small audio, create run, processor
  completion, JSON/Markdown artifacts, DB document chunks, object-store files,
  no object-key leakage, and temp cleanup.

Verification:

- Run `make compose-test`.
- Stop for review and commit.

## Stage 7: Operational Hardening

Stage goal: close failure-mode, observability, cleanup, and recovery gaps before
any public deployment.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 7.1: Logging, Cleanup, And Failure Modes

Phase goal: make the system observable without leaking sensitive content.

Phase stop rule: stop after every numbered step in this phase.

#### Step 7.1.1: Harden Structured Logging And Redaction

Tests first:

- Add tests proving logs/events do not include secrets, prompts, raw
  transcripts, full translations, article bodies, full artifacts, or object
  keys.

Implementation:

- Extend logging/redaction helpers for API and processor.

Verification:

- Run logging/redaction tests.
- Stop for review and commit.

#### Step 7.1.2: Add Cleanup Scheduler

Tests first:

- Add tests for expired uploads, chunks, abandoned multipart uploads, expired
  sessions, revoked tokens, failed cleanup retries, and idempotency.

Implementation:

- Implement cleanup scheduler/tasks.

Verification:

- Run cleanup scheduler tests.
- Stop for review and commit.

#### Step 7.1.3: Add Failure-Mode Tests

Tests first:

- Add tests for database outage, queue outage, object-store outage, provider
  failure, retry exhaustion, partial artifact write, and cleanup failure.

Implementation:

- Implement only the changes needed to make failure behavior safe and
  observable.

Verification:

- Run failure-mode tests.
- Stop for review and commit.

### Phase 7.2: Operations Guidance

Phase goal: prepare for safe operation and recovery.

Phase stop rule: stop after every numbered step in this phase.

#### Step 7.2.1: Add Backup, Restore, And Operator Docs

Tests first: not applicable for behavior.

Implementation:

- Document user creation, token revocation, retry, cancellation, cleanup,
  backups, restore, smoke tests, and cost monitoring.

Verification:

- Run `git diff --check`.
- Stop for review and commit.

## Stage 8: Cloudflare Adapter Spike

Stage goal: evaluate Cloudflare as a replaceable adapter target without moving
core business rules into Cloudflare-specific code.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 8.1: Cloudflare Limits And R2

Phase goal: start with the most appropriate Cloudflare adapter: object storage.

Phase stop rule: stop after every numbered step in this phase.

#### Step 8.1.1: Re-Verify Cloudflare Limits

Tests first: not applicable for behavior.

Implementation:

- Re-check official docs for Workers, Python Workers, D1, R2, and Queues.
- Update cloud deployment notes with current limits and dates.

Verification:

- Run `git diff --check`.
- Stop for review and commit.

#### Step 8.1.2: Prototype R2 ObjectStore Adapter

Tests first:

- Reuse the `ObjectStore` contract tests.
- Add R2-specific config tests where needed.

Implementation:

- Implement or prototype `CloudflareR2ObjectStore`.
- Do not change core object-store interfaces unless contract tests prove a real
  interface gap.

Verification:

- Run object-store contract tests against R2 or a staging equivalent.
- Stop for review and commit.

### Phase 8.2: Metadata, Queue, And Thin Worker Decisions

Phase goal: decide whether additional Cloudflare adapters are practical.

Phase stop rule: stop after every numbered step in this phase.

#### Step 8.2.1: Decide External PostgreSQL Versus D1

Tests first:

- Run metadata contract tests against any D1-compatible local/staging setup
  before accepting D1.

Implementation:

- Document D1 versus external PostgreSQL tradeoffs for storage, SQL semantics,
  concurrency, row limits, migrations, and portability.
- Select the first Cloudflare metadata strategy.

Verification:

- Run relevant metadata contract tests.
- Stop for review and commit.

#### Step 8.2.2: Prototype Cloudflare Queue Adapter If Still Appropriate

Tests first:

- Reuse the `JobQueue` lifecycle contract tests.

Implementation:

- Prototype Cloudflare Queue adapter only after job semantics are stable.
- Keep persisted job state authoritative.

Verification:

- Run queue contract tests.
- Stop for review and commit.

#### Step 8.2.3: Prototype Thin Worker API Or Proxy If Still Appropriate

Tests first:

- Add contract tests proving the Worker implements or proxies the same OpenAPI
  behavior for selected routes.

Implementation:

- Prototype only thin ingress/proxy behavior.
- Do not move business rules into Worker code.
- Audit dependencies carefully.

Verification:

- Run API contract tests for Worker/proxy behavior.
- Stop for review and commit.

## Stage 9: First Production Deployment

Stage goal: deploy the proven API/processor architecture with one selected
cloud topology and rollback/operations path.

Stage stop rule: complete only one step, verify it, summarize it, and stop for
review before the next step.

### Phase 9.1: Provisioning

Phase goal: create production resources without deploying unverified code.

Phase stop rule: stop after every numbered step in this phase.

#### Step 9.1.1: Pick And Document Final Runtime Topology

Tests first: not applicable for behavior.

Implementation:

- Document chosen API runtime, processor runtime, metadata DB, object store,
  queue, secrets provider, frontend hosting, DNS/WAF, and backup strategy.

Verification:

- Run `git diff --check`.
- Stop for review and commit.

#### Step 9.1.2: Provision Secrets, Object Storage, Metadata DB, And Queue

Tests first:

- Add smoke checks for connectivity and least-privilege access where practical.

Implementation:

- Provision production or staging resources.
- Do not deploy the full app until smoke checks pass.

Verification:

- Run provisioning smoke checks.
- Stop for review and commit.

### Phase 9.2: Deploy And Smoke Test

Phase goal: deploy safely with fake-provider smoke tests first.

Phase stop rule: stop after every numbered step in this phase.

#### Step 9.2.1: Deploy API And Processor With Fake Provider

Tests first:

- Use existing smoke/E2E tests against the production topology in fake-provider
  mode.

Implementation:

- Deploy API and processor.
- Enable cleanup schedule.
- Configure monitoring/alerts for API errors, processor failures, queue
  backlog, object cleanup, DB health, and provider spend.

Verification:

- Run fake-provider smoke tests.
- Stop for review and commit.

#### Step 9.2.2: Run One Controlled Real-Provider Test

Tests first:

- Use tiny fixtures and a hard budget cap.

Implementation:

- Enable real-provider mode only for the controlled test.
- Verify artifacts, usage ledger, costs, logs, and cleanup.

Verification:

- Run the controlled real-provider test.
- Stop for review and commit.

#### Step 9.2.3: Validate Rollback And Restore Procedures

Tests first:

- Define rollback/restore smoke checks before running them.

Implementation:

- Execute or dry-run rollback and restore procedures.
- Update operations docs with any corrections.

Verification:

- Run rollback/restore smoke checks or documented dry-run.
- Stop for review and commit.

## Final Acceptance Criteria

Local acceptance:

- `docker compose` starts API, processor, PostgreSQL, MinIO, and Redis/Valkey.
- A preconfigured user can log in.
- A user can upload audio through the same object-store flow used in production.
- A user can create a run.
- Processor completes the fake-provider pipeline.
- User can list runs and artifacts.
- User can retrieve JSON and Markdown artifacts.
- User cannot access another user's resources.
- Public API rejects prompt/text/artifact/model/provider/object-key payloads.
- Public API responses do not expose raw object keys.
- Object keys contain opaque IDs and never contain original or sanitized upload
  filenames.
- Temporary objects are deleted after completion or cleanup expiry.
- Existing CLI, stage, fake-provider, and Makefile tests still pass.

Architecture acceptance:

- No core domain/application code imports Cloudflare, AWS, GCP, Azure, MinIO,
  Redis, or provider SDKs directly.
- Cloud-specific code lives only under adapter modules.
- Adapter contract tests pass for in-memory and local adapters.
- SQL migrations run cleanly from an empty database.
- Job retries are idempotent.
- Cancellation works between stages.
- Budget enforcement survives processor restart.
- Provider config is versioned and run retries use the original run snapshot.
- Queue messages contain only `job_id` plus safe routing metadata.

Security acceptance:

- Passwords and bearer tokens are hashed at rest.
- Processor/service tokens are separate from user sessions and are hashed or
  certificate-backed at rest.
- Session cookies are HTTP-only and secure in production.
- CORS is allowlisted.
- CSRF is addressed for cookie-authenticated writes.
- Auth, upload, and run-creation endpoints are rate limited.
- Upload size, multipart metadata, content type, extension, checksum, and
  ownership are enforced server-side.
- Logs do not include secrets, raw transcript text, full translations, article
  bodies, prompts, object keys, or complete artifact payloads.
- Prompt-injection fixtures are covered by tests.

Cloud readiness acceptance:

- R2 or another object-store adapter passes `ObjectStore` contract tests.
- Metadata adapter choice is documented with limit tradeoffs.
- Queue adapter passes job lifecycle contract tests or is explicitly rejected.
- Processor runtime is outside Workers or otherwise proven to support
  `ffmpeg`, local scratch files, and long-running work.
