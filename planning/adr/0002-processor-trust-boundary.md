# ADR 0002: Processor Trust Boundary

Date: 2026-06-06

Status: Accepted

## Context

The API migration separates request handling from long-running pipeline work.
The processor will run audio and model stages asynchronously and must not become
an alternate privileged API surface.

This ADR follows:

- [Cloud-Agnostic API Conversion Plan](../cloud_agnostic_api_conversion_plan.md)
- [Cloud-Agnostic API Conversion Stepwise Commit Plan](../cloudflare/backend_api/cloud_agnostic_api_conversion_stepwise_commit_plan.md)
- [ADR 0001: Canonical Runtime Shape](0001-canonical-runtime-shape.md)

## Decision

Queue messages contain only `job_id` plus safe routing metadata. Safe routing
metadata may include values such as queue name, stage name, priority, retry
hint, lease hint, and correlation ID. Queue messages must not contain prompts,
prompt IDs, model IDs, provider IDs, raw transcript text, raw translation text,
raw article text, artifact JSON, object keys, bearer tokens, session data, or
user IDs used for ownership decisions.

The persisted job table is authoritative for job state, leases, retries,
cancellation, terminal outcomes, ownership scope, and processor eligibility.
The processor must load the job record by `job_id` and follow the persisted
state machine rather than trusting queue payload state.

The processor authenticates as a service identity that is separate from browser
sessions, user bearer tokens, and provider credentials. Service credentials are
issued and revoked independently from user credentials.

Processor access is least-privilege. A processor identity can perform only the
job and artifact operations needed for assigned processor work. It cannot mint
users, mint user tokens, bypass public API ownership checks, change user
ownership, or perform administrative account actions.

## Consequences

- The queue backend remains replaceable because queue payloads are small and do
  not carry business state.
- Retrying or redelivering a queue message cannot override the persisted job
  state.
- Public API authorization and processor authorization can evolve separately.
- Operational logs for queue messages avoid raw model inputs, artifacts, object
  keys, and user ownership data.
