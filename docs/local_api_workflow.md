# Local API Workflow

This document covers the local API-backed workflow for the backend conversion
track. The local default is fake-provider mode, so setup and route exploration
should not require a paid model call.

Current status:

- `docker compose` validates the API, processor, PostgreSQL, MinIO, Redis, and
  optional Nginx proxy service topology.
- `make compose-test` is wired as the stack smoke target, but it still needs a
  running Docker/Colima daemon.
- The API container currently starts the FastAPI app and exposes `/health`.
  Full route execution still needs production `AppState` adapter wiring from
  environment into the container.
- The processor service currently validates `urdu-pipeline process --dry-run`,
  writes `/tmp/processor-ready`, and stays alive. The long-running processor
  CLI loop is not wired yet.

## Start And Setup

Copy the local example env if you need overrides:

```bash
cp .env.local.example .env.local
```

The local stack defaults to fake-provider mode:

```env
PIPELINE_PROVIDER_MODE=fake
SERVICE_AUTH_TOKEN=local_processor_dev_token_change_me
```

`SERVICE_AUTH_TOKEN` is the shared service credential used by the processor for
internal/service-authenticated API calls. Change it before any non-local use.

Bring up the local parity stack and run setup:

```bash
make compose-setup
```

That runs these steps in order:

```bash
make compose-up
make compose-migrate
make compose-seed-bucket
make compose-seed-user
make compose-seed-service-identity
make compose-seed-provider-config
```

Useful overrides:

```bash
make compose-seed-user LOCAL_USERNAME=alice LOCAL_PASSWORD='change-me'
make compose-seed-provider-config PROVIDER_NAME=fake
make compose-seed-bucket OBJECT_STORE_BUCKET=urdu-pipeline-local
```

Smoke-check the stack:

```bash
make compose-test
```

Stop the stack:

```bash
make compose-down
```

## Session Login

Use `POST /auth/login` with a preconfigured local user:

```bash
curl -i \
  -c /tmp/urdu-api.cookies \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:8000/auth/login \
  -d '{"username":"local_user","password":"local_password_change_me"}'
```

Successful login sets:

- `session`: HTTP-only session cookie.
- `csrf_token`: readable CSRF cookie.

For session-authenticated mutating requests, read `csrf_token` from the cookie
jar and send it as `X-CSRF-Token`.

Bearer token requests use `Authorization: Bearer <token>` and do not need CSRF.

## Token Creation

Create a bearer token with `POST /tokens` while logged in with a session cookie:

```bash
CSRF_TOKEN="$(awk '$6 == "csrf_token" {print $7}' /tmp/urdu-api.cookies)"

curl -s \
  -b /tmp/urdu-api.cookies \
  -H "X-CSRF-Token: ${CSRF_TOKEN}" \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:8000/tokens \
  -d '{"name":"local-cli","description":"local smoke token"}'
```

The raw token is shown once. Store it only in a local shell variable or secret
store:

```bash
API_TOKEN='paste-token-here'
```

List token summaries with `GET /tokens`; raw token values and hashes are never
returned.

## Upload Audio

For small files, use direct upload with `POST /uploads/direct`:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -F "file=@inputs/sample.mp3;type=audio/mpeg" \
  http://localhost:8000/uploads/direct
```

For larger files, use the signed upload flow with `POST /uploads/init`:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:8000/uploads/init \
  -d '{"filename":"lecture.mp3","content_type":"audio/mpeg","size_bytes":1234567}'
```

The response includes `upload_id` and `upload_url`. PUT the audio bytes to the
signed URL, then complete the upload:

```bash
curl -X PUT --upload-file inputs/sample.mp3 '<upload_url>'

curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:8000/uploads/<upload_id>/complete \
  -d '{}'
```

Public API responses never expose raw object keys. Object keys are derived
server-side from opaque IDs; original filenames remain metadata only.

## Create And Poll A Run

Create a run with `POST /runs`:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -H 'Content-Type: application/json' \
  -X POST http://localhost:8000/runs \
  -d '{"upload_id":"<upload_id>","description":"local fake-provider run"}'
```

Poll run state with `GET /runs/{run_id}`:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  http://localhost:8000/runs/<run_id>
```

Poll stage events with `GET /runs/{run_id}/events`:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  http://localhost:8000/runs/<run_id>/events
```

Event persistence is currently a placeholder; the endpoint exists for client
polling shape.

## Read And Download Artifacts

List artifacts for a run with `GET /runs/{run_id}/artifacts`:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  http://localhost:8000/runs/<run_id>/artifacts
```

Read artifact metadata with `GET /artifacts/{artifact_id}`:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  http://localhost:8000/artifacts/<artifact_id>
```

Request a signed JSON download URL with `GET /artifacts/{artifact_id}/download`:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  'http://localhost:8000/artifacts/<artifact_id>/download?format=json'
```

Request a signed Markdown download URL:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  'http://localhost:8000/artifacts/<artifact_id>/download?format=markdown'
```

Download responses return signed URLs, not object keys.

## Cancellation

Cancel pending or running work with `POST /runs/{run_id}/cancel`:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -X POST http://localhost:8000/runs/<run_id>/cancel
```

The processor lifecycle code checks cancellation between chunks/stages. Until
the long-running processor command is wired, this is an API state transition and
client-contract check.

## Retry And Cleanup

Retry behavior is processor-owned:

- transient failures are retried through the job lifecycle;
- duplicate queue messages are ignored when persisted job state is terminal;
- stage idempotency checks existing artifacts before rerunning work;
- usage records use idempotency keys to avoid double-counting costs.

Cleanup behavior is also processor-owned:

- success or permanent failure deletes run-scoped temporary objects under
  `tmp/users/{user_id}/runs/{run_id}/`;
- retryable failures preserve temporary objects for the next attempt;
- local workspace cleanup runs even when object-store cleanup fails.

Operator cleanup command scheduling is not implemented yet. Use `make
compose-down` to stop the local stack when finished; named volumes are preserved
so database, MinIO, and Redis data survive between runs unless explicitly
removed with Docker volume commands.

## Current Local Limits

- Do not use `.env.local.example` secrets outside local development.
- The local fake-provider mode is the intended default until real-provider
  smoke tests are explicitly planned.
- The local stack does not yet run a full fake-provider API-to-processor E2E;
  that is Step 6.2.3.
- The processor command shell still needs the real long-running loop before
  compose can complete queued jobs.
