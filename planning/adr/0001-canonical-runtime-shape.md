# ADR 0001: Canonical Runtime Shape

Date: 2026-06-06

Status: Accepted

## Context

The current application is a local CLI and Streamlit prototype for Urdu
speech-to-text, translation, and article generation. The next architecture needs
to support a secure API-backed application while preserving the existing CLI,
fake providers, artifact schemas, cache behavior, cost controls, and safe
integration tests.

Cloudflare Free is a likely early deployment target, but Cloudflare Workers and
related services have constraints that make them unsuitable as the source of
truth for the full pipeline architecture. The broader plan requires cloud
provider adapters to remain replaceable.

This ADR follows:

- [Cloud-Agnostic API Conversion Plan](../cloud_agnostic_api_conversion_plan.md)
- [Cloud-Agnostic API Conversion Stepwise Commit Plan](../cloudflare/backend_api/cloud_agnostic_api_conversion_stepwise_commit_plan.md)

## Decision

FastAPI running on regular CPython is the canonical API runtime for the
application.

The Python processor is a separate runtime from the API. Long-running pipeline
work, audio chunking, and any execution that depends on `ffmpeg` or `ffprobe`
belongs in the processor runtime, not in the API request path.

PostgreSQL is the canonical metadata database for local parity and portable
deployment. Alternative metadata stores may be added later only as adapters that
preserve the same application contracts.

Object storage is accessed through an S3-compatible object-store port. MinIO is
the expected local implementation; cloud object stores such as R2 or S3 are
deployment adapters.

Cloudflare is a deployment option and adapter boundary, not the business logic
source of truth. Any Cloudflare Worker or Pages Function should act as thin
ingress or implement the same API contract; core domain and application logic
must remain independent of Cloudflare-specific services.

## Consequences

- Core domain and application modules must not import Cloudflare, AWS, GCP,
  Azure, MinIO, Redis, provider SDK, or web framework implementations.
- FastAPI route handlers should delegate business decisions to application
  services and ports rather than encoding provider-specific behavior directly.
- Processor behavior can be tested and deployed independently from API request
  handling.
- Local development should prioritize the same shape as production: API,
  processor, PostgreSQL, object storage, and queue services as separate
  components.
