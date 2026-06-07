# Backend Operator Guide

This guide covers operational procedures for the API-backed backend conversion
track. The local default remains fake-provider mode; use real-provider mode only
after secrets, budgets, and smoke checks are intentionally configured.

## User And Service Setup

Create or update a local login user:

```bash
make compose-seed-user LOCAL_USERNAME=alice LOCAL_PASSWORD='change-me'
```

Create or update a user against a specific database:

```bash
PYTHONPATH=src .venv/bin/python -m urdu_pipeline.cli admin-create-user \
  --username alice \
  --password 'change-me' \
  --database-url "$DATABASE_URL"
```

List users, reset a password, or disable a user:

```bash
PYTHONPATH=src .venv/bin/python -m urdu_pipeline.cli admin-list-users \
  --database-url "$DATABASE_URL"

PYTHONPATH=src .venv/bin/python -m urdu_pipeline.cli admin-reset-password \
  --user-id usr_<hex> \
  --new-password 'new-secret' \
  --database-url "$DATABASE_URL"

PYTHONPATH=src .venv/bin/python -m urdu_pipeline.cli admin-disable-user \
  --user-id usr_<hex> \
  --database-url "$DATABASE_URL"
```

Create the processor service identity:

```bash
make compose-seed-service-identity SERVICE_IDENTITY_NAME=processor
```

Revoke a service identity if its operational credentials are no longer trusted:

```bash
PYTHONPATH=src .venv/bin/python -m urdu_pipeline.cli admin-revoke-service-identity \
  --service-identity-id svc_<hex> \
  --database-url "$DATABASE_URL"
```

Rotate `SERVICE_AUTH_TOKEN` separately in the runtime secret source. The token
is an environment secret used for internal service authentication; it is not
stored in the service identity table.

## Bearer Token Revocation

Bearer token management is intentionally session-authenticated. Bearer tokens
can use resource routes, but they cannot create or revoke other bearer tokens.

List token summaries:

```bash
curl -s \
  -b /tmp/urdu-api.cookies \
  -H "X-CSRF-Token: ${CSRF_TOKEN}" \
  http://localhost:8000/tokens
```

Revoke one token:

```bash
curl -s \
  -b /tmp/urdu-api.cookies \
  -H "X-CSRF-Token: ${CSRF_TOKEN}" \
  -X DELETE http://localhost:8000/tokens/tok_<hex>
```

The API returns only token metadata. Raw token values are shown once at
creation and are never stored server-side.

## Retry And Recovery

Processor retry is automatic:

- transient provider or infrastructure failures are requeued until the
  configured attempt limit is reached;
- retry reasons and dead-letter reasons are redacted before persistence;
- terminal jobs are not processed again if duplicate queue messages appear;
- stage idempotency checks existing durable artifacts before repeating work;
- usage records use idempotency keys to prevent double-counting cost.

Use run events and job/run status to decide whether to wait, cancel, or create
a new run. Do not edit queue or job rows manually while a processor is running.
If a run has failed permanently, create a new run from the same completed upload
instead of resetting the failed job in place.

Run one processor claim locally for a controlled retry/recovery check:

```bash
SERVICE_AUTH_TOKEN="$SERVICE_AUTH_TOKEN" \
PYTHONPATH=src .venv/bin/python -m urdu_pipeline.cli process \
  --api-url http://localhost:8000 \
  --once
```

## Cancellation

Cancel pending or running work with:

```bash
curl -s \
  -H "Authorization: Bearer ${API_TOKEN}" \
  -X POST http://localhost:8000/runs/run_<hex>/cancel
```

The API transitions the run to `cancelled`. The processor checks persisted
state between stages and chunks, so a currently active provider call may finish
before the cancellation is observed. Treat cancellation as cooperative, not as
an immediate process kill.

## Cleanup

Cleanup has a scheduler function and durable cleanup task records. The current
task types are:

- expire abandoned uploads and delete `uploads/{upload_id}`;
- abort abandoned multipart uploads;
- delete terminal-run temporary objects under
  `tmp/users/{user_id}/runs/{run_id}/`;
- purge expired sessions;
- purge revoked bearer tokens after retention.

Cleanup failures are retried. Final failures are marked `failed` with a
redacted `last_error` for operator review.

There is not yet a dedicated CLI or Make target for the cleanup scheduler. Wire
`urdu_pipeline.processor.cleanup_scheduler.run_cleanup_scheduler` into a
maintenance worker using the same PostgreSQL and object-store settings as the
processor. Until that exists, verify cleanup behavior through unit tests and
avoid deleting object-store prefixes by hand unless the owning run/upload state
is already terminal or expired.

## Backups

Back up PostgreSQL before schema migrations, deploys, or manual recovery work:

```bash
pg_dump "$DATABASE_URL" --format=custom --file "backups/urdu_pipeline_$(date +%Y%m%d_%H%M%S).dump"
```

For local Docker PostgreSQL, run `pg_dump` from a host with access to the
published database port or from inside the database container.

Back up object storage separately from PostgreSQL. For S3-compatible stores,
copy the bucket to a backup bucket or archive prefix with the provider's native
tooling. Preserve these prefixes together:

- `uploads/`
- `artifacts/`
- `tmp/` only when investigating retry state; terminal-run tmp objects are
  disposable after cleanup.

Back up Redis/Valkey only if preserving in-flight queue leases matters for the
maintenance window. PostgreSQL remains the source of truth for users, uploads,
runs, jobs, artifacts, usage, and cleanup tasks.

## Restore

Restore into a clean database, then run migrations to bring the schema to the
current application version:

```bash
createdb urdu_pipeline_restore
pg_restore --dbname "$RESTORE_DATABASE_URL" backups/urdu_pipeline_<timestamp>.dump
PYTHONPATH=src .venv/bin/python -m urdu_pipeline.cli migrate-db \
  --database-url "$RESTORE_DATABASE_URL"
```

Restore object storage before resuming processors. Artifacts and uploads in
PostgreSQL reference object-store objects by server-generated keys, so missing
objects will surface as artifact/download or processor materialization errors.

After restore:

- start the API first and check `/health`;
- run migrations again to confirm they skip cleanly;
- start one processor with `--once` before enabling the normal processor loop;
- run the fake-provider smoke test if the restored environment is local;
- verify token/session revocation state if the restore point is old.

## Smoke Tests

Local stack smoke:

```bash
make compose-test
```

API health check:

```bash
curl -fsS http://localhost:8000/health
```

Fake-provider API-to-processor E2E inside compose:

```bash
make compose-fake-provider-e2e
```

Optional live adapter smokes are skipped by default and require explicit
environment flags:

- `RUN_POSTGRES_MIGRATION_SMOKE=1`
- `RUN_REDIS_JOB_QUEUE_SMOKE=1`
- `RUN_MINIO_OBJECT_STORE_SMOKE=1`
- `RUN_S3_OBJECT_STORE_SMOKE=1`

Run them only against disposable local or staging resources.

For AWS S3-specific setup, IAM, encryption, and smoke-test details, see
`docs/aws_s3_object_store.md`.

## Cost Monitoring

In fake-provider mode, cost records are deterministic test data. In real
provider mode:

- set `DEFAULT_BUDGET_USD`, `HARD_CAP_USD`, and `COST_SAFETY_MARGIN`;
- seed provider config after changing provider/model settings;
- monitor usage ledger rows by `run_id`, `job_id`, `provider_name`, and
  `model_id`;
- investigate repeated retries before increasing attempt limits because each
  retry may repeat provider work unless stage idempotency finds durable
  artifacts;
- reconcile provider invoices against usage ledger totals before deleting old
  usage records.

Prefer disabling real-provider mode over raising budgets during an incident.
