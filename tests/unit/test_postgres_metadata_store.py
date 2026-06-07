"""PostgreSQL metadata adapter contract tests for user/auth/upload/run records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from urdu_pipeline.application.ports import (
    ArtifactRecord,
    BearerTokenRecord,
    CacheScope,
    JobRecord,
    ProviderConfigSnapshot,
    RunRecord,
    SessionRecord,
    ServiceIdentityRecord,
    UploadRecord,
    UserRecord,
    UsageRecord,
)
from urdu_pipeline.domain import (
    ArtifactId,
    ArtifactStage,
    ArtifactType,
    CleanupTaskId,
    CleanupTaskStatus,
    JobId,
    JobStatus,
    ProviderConfigStatus,
    ProviderConfigVersionId,
    ProviderRunId,
    RunId,
    RunStatus,
    SessionId,
    TokenId,
    ServiceIdentityId,
    ServiceIdentityStatus,
    UploadId,
    UploadStatus,
    UserId,
    UserStatus,
)
from urdu_pipeline.infrastructure.db.metadata import (
    ArtifactDocumentChunkRecord,
    CleanupTaskRecord,
    PostgresMetadataStore,
    PromptVersionRecord,
    StageEventRecord,
)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self._row: tuple[Any, ...] | None = None
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args) -> bool:
        return False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.connection.executed.append((sql, params))
        statement = " ".join(sql.lower().split())
        if statement.startswith("insert into users"):
            self.connection.maybe_fail("users")
            assert params is not None
            self.connection.users[params[0]] = params
        elif statement.startswith("select user_id, username") and "where username = %s" in statement:
            assert params is not None
            (username,) = params
            row = next(
                (row for row in self.connection.users.values() if row[1] == username),
                None,
            )
            self._row = (
                (row[0], row[1], row[2], row[3], row[4]) if row is not None else None
            )
        elif statement.startswith("select user_id, username") and "order by created_at, user_id" in statement:
            self._rows = sorted(
                [
                    (row[0], row[1], row[2], row[3], row[4])
                    for row in self.connection.users.values()
                ],
                key=lambda row: (row[-1], row[0]),
            )
        elif statement.startswith("select user_id, username"):
            assert params is not None
            row = self.connection.users.get(params[0])
            self._row = (
                (row[0], row[1], row[2], row[3], row[4]) if row is not None else None
            )
        elif statement.startswith("update users"):
            assert params is not None
            username, status, password_hash, _updated_at, user_id = params
            row = self.connection.users.get(user_id)
            if row is not None:
                self.connection.users[user_id] = (
                    user_id,
                    username,
                    status,
                    password_hash,
                    row[4],
                    _updated_at,
                )
        elif statement.startswith("insert into service_identities"):
            self.connection.maybe_fail("service_identities")
            assert params is not None
            self.connection.service_identities[params[0]] = params
        elif statement.startswith("select service_identity_id") and "where name = %s" in statement:
            assert params is not None
            (name,) = params
            self._row = next(
                (row[:4] for row in self.connection.service_identities.values() if row[1] == name),
                None,
            )
        elif statement.startswith("select service_identity_id"):
            assert params is not None
            row = self.connection.service_identities.get(params[0])
            self._row = row[:4] if row is not None else None
        elif statement.startswith("update service_identities"):
            assert params is not None
            name, status, _updated_at, service_identity_id = params
            row = self.connection.service_identities.get(service_identity_id)
            if row is not None:
                self.connection.service_identities[service_identity_id] = (
                    service_identity_id,
                    name,
                    status,
                    row[3],
                    row[4],
                )
        elif statement.startswith("insert into sessions"):
            assert params is not None
            session_id, user_id, token_hash, expires_at, revoked_at, created_at = params
            self.connection.sessions[session_id] = (
                session_id,
                user_id,
                token_hash,
                expires_at,
                created_at,
                revoked_at,
            )
            self.connection.sessions_by_hash[token_hash] = session_id
        elif statement.startswith("select session_id"):
            assert params is not None
            (token_hash,) = params
            session_id = self.connection.sessions_by_hash.get(token_hash)
            self._row = self.connection.sessions.get(session_id) if session_id else None
        elif statement.startswith("update sessions"):
            assert params is not None
            revoked_at, session_id = params
            row = self.connection.sessions.get(session_id)
            if row is not None:
                updated = (row[0], row[1], row[2], row[3], row[4], revoked_at)
                self.connection.sessions[session_id] = updated
        elif statement.startswith("insert into api_tokens"):
            assert params is not None
            (
                token_id,
                user_id,
                token_hash,
                name,
                description,
                expires_at,
                revoked_at,
                created_at,
                last_used_at,
            ) = params
            row = (
                token_id,
                user_id,
                token_hash,
                name,
                description,
                created_at,
                expires_at,
                revoked_at,
                last_used_at,
            )
            self.connection.api_tokens[token_id] = row
            self.connection.api_tokens_by_hash[token_hash] = token_id
        elif statement.startswith("select api_token_id") and "where token_hash = %s" in statement:
            assert params is not None
            (token_hash,) = params
            token_id = self.connection.api_tokens_by_hash.get(token_hash)
            self._row = self.connection.api_tokens.get(token_id) if token_id else None
        elif statement.startswith("select api_token_id") and "where api_token_id = %s" in statement:
            assert params is not None
            self._row = self.connection.api_tokens.get(params[0])
        elif statement.startswith("select api_token_id") and "where user_id = %s" in statement:
            assert params is not None
            (user_id,) = params
            self._rows = [
                row for row in self.connection.api_tokens.values() if row[1] == user_id
            ]
        elif statement.startswith("update api_tokens"):
            assert params is not None
            (
                token_hash,
                name,
                description,
                expires_at,
                revoked_at,
                last_used_at,
                token_id,
            ) = params
            row = self.connection.api_tokens.get(token_id)
            if row is not None:
                updated = (
                    token_id,
                    row[1],
                    token_hash,
                    name,
                    description,
                    row[5],
                    expires_at,
                    revoked_at,
                    last_used_at,
                )
                self.connection.api_tokens[token_id] = updated
                self.connection.api_tokens_by_hash[token_hash] = token_id
        elif statement.startswith("insert into uploads"):
            self.connection.maybe_fail("uploads")
            assert params is not None
            self.connection.uploads[params[1]] = params
        elif (
            statement.startswith("select user_id, upload_id, status")
            and "order by created_at, upload_id" in statement
        ):
            assert params is not None
            (user_id,) = params
            self._rows = [
                row for row in self.connection.uploads.values() if row[0] == user_id
            ]
        elif statement.startswith("select user_id, upload_id"):
            assert params is not None
            user_id, upload_id = params
            row = self.connection.uploads.get(upload_id)
            self._row = row if row is not None and row[0] == user_id else None
        elif statement.startswith("update uploads"):
            assert params is not None
            (
                status,
                original_filename,
                content_type,
                size_bytes,
                multipart_upload_id,
                _status_probe,
                _completed_at,
                user_id,
                upload_id,
            ) = params
            row = self.connection.uploads.get(upload_id)
            if row is not None and row[0] == user_id:
                self.connection.uploads[upload_id] = (
                    user_id,
                    upload_id,
                    status,
                    original_filename,
                    content_type,
                    size_bytes,
                    multipart_upload_id,
                    row[-1],
                )
        elif statement.startswith("insert into runs"):
            self.connection.maybe_fail("runs")
            assert params is not None
            self.connection.runs[params[1]] = params
        elif statement.startswith("insert into jobs"):
            self.connection.maybe_fail("jobs")
            assert params is not None
            user_id, run_id, job_id, stage, status, created_at, queued_at = params
            self.connection.jobs[job_id] = {
                "user_id": user_id,
                "run_id": run_id,
                "job_id": job_id,
                "stage": stage,
                "status": status,
                "routing": {},
                "lease_owner_service_identity_id": None,
                "lease_id": None,
                "lease_expires_at": None,
                "created_at": created_at,
                "queued_at": queued_at,
                "started_at": None,
                "completed_at": None,
            }
        elif statement.startswith("select user_id, run_id, job_id"):
            assert params is not None
            if len(params) == 1:
                (job_id,) = params
                user_id = None
            else:
                user_id, job_id = params
            job = self.connection.jobs.get(job_id)
            if job is None or (user_id is not None and job["user_id"] != user_id):
                self._row = None
            else:
                self._row = (
                    job["user_id"],
                    job["run_id"],
                    job["job_id"],
                    job["status"],
                    job["created_at"],
                )
        elif statement.startswith("update jobs") and "set status = %s" in statement:
            assert params is not None
            status, _status_probe, completed_at, user_id, job_id = params
            job = self.connection.jobs.get(job_id)
            if job is not None and job["user_id"] == user_id:
                job["status"] = status
                if status in {"succeeded", "failed", "cancelled", "dead_lettered"}:
                    job["completed_at"] = job["completed_at"] or completed_at
        elif statement.startswith("update jobs") and "set status = 'claimed'" in statement:
            assert params is not None
            (
                worker_id,
                lease_id,
                expires_at,
                started_at,
                job_id,
                now,
            ) = params
            job = self.connection.jobs.get(job_id)
            if (
                job is None
                or job["status"] not in {"queued", "claimed", "running"}
                or (
                    job["status"] != "queued"
                    and job["lease_expires_at"] is not None
                    and job["lease_expires_at"] > now
                )
                or (
                    job["status"] != "queued"
                    and job["lease_expires_at"] is None
                )
            ):
                self._row = None
            else:
                job["status"] = "claimed"
                job["lease_owner_service_identity_id"] = worker_id
                job["lease_id"] = lease_id
                job["lease_expires_at"] = expires_at
                job["started_at"] = job["started_at"] or started_at
                self._row = (
                    job["user_id"],
                    job["run_id"],
                    job["job_id"],
                    job["lease_id"],
                    job["lease_expires_at"],
                    job["routing"],
                )
        elif statement.startswith("select coalesce(max(attempt_number)"):
            assert params is not None
            (job_id,) = params
            self._row = (self.connection.attempts_by_job.get(job_id, 0) + 1,)
        elif statement.startswith("insert into job_attempts"):
            assert params is not None
            (
                _job_attempt_id,
                job_id,
                user_id,
                run_id,
                attempt_number,
                status,
                worker_id,
                lease_id,
                started_at,
                created_at,
            ) = params
            self.connection.attempts_by_job[job_id] = attempt_number
            self.connection.job_attempts[(job_id, lease_id)] = {
                "user_id": user_id,
                "run_id": run_id,
                "attempt_number": attempt_number,
                "status": status,
                "worker_id": worker_id,
                "lease_id": lease_id,
                "started_at": started_at,
                "created_at": created_at,
                "completed_at": None,
                "error_message": None,
            }
        elif statement.startswith("update jobs") and "set lease_expires_at = %s" in statement:
            assert params is not None
            expires_at, job_id, lease_id, now = params
            job = self.connection.jobs.get(job_id)
            if (
                job is None
                or job["lease_id"] != lease_id
                or job["status"] not in {"claimed", "running"}
                or job["lease_expires_at"] <= now
            ):
                self._row = None
            else:
                job["lease_expires_at"] = expires_at
                self._row = (
                    job["job_id"],
                    job["lease_id"],
                    job["lease_expires_at"],
                    job["routing"],
                )
        elif statement.startswith("update jobs") and "set status = 'queued'" in statement:
            assert params is not None
            queued_at, job_id, lease_id = params
            job = self.connection.jobs.get(job_id)
            if job is None or job["lease_id"] != lease_id:
                self._row = None
            else:
                job["status"] = "queued"
                job["lease_owner_service_identity_id"] = None
                job["lease_id"] = None
                job["lease_expires_at"] = None
                job["queued_at"] = queued_at
                self._row = (job["job_id"],)
        elif statement.startswith("update jobs") and "set status = 'failed'" in statement:
            assert params is not None
            completed_at, job_id, lease_id = params
            self._update_terminal_job(job_id, lease_id, "failed", completed_at)
        elif statement.startswith("update jobs") and "set status = 'succeeded'" in statement:
            assert params is not None
            completed_at, job_id, lease_id = params
            self._update_terminal_job(job_id, lease_id, "succeeded", completed_at)
        elif statement.startswith("update jobs") and "set status = 'dead_lettered'" in statement:
            assert params is not None
            completed_at, job_id, lease_id = params
            self._update_terminal_job(job_id, lease_id, "dead_lettered", completed_at)
        elif statement.startswith("update jobs") and "set status = 'cancelled'" in statement:
            assert params is not None
            completed_at, job_id = params
            job = self.connection.jobs.get(job_id)
            if job is None or job["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "dead_lettered",
            }:
                self._row = None
            else:
                job["status"] = "cancelled"
                job["lease_owner_service_identity_id"] = None
                job["lease_id"] = None
                job["lease_expires_at"] = None
                job["completed_at"] = completed_at
                self._row = (job["job_id"],)
        elif statement.startswith("update job_attempts"):
            assert params is not None
            status, completed_at, reason, job_id, lease_id = params
            attempt = self.connection.job_attempts.get((job_id, lease_id))
            if attempt is not None:
                attempt["status"] = status
                attempt["completed_at"] = completed_at
                attempt["error_message"] = reason
        elif statement.startswith("insert into artifacts"):
            assert params is not None
            user_id, run_id, job_id, artifact_id, stage, artifact_type, object_key, manifest, created_at = params
            self.connection.artifacts[artifact_id] = {
                "user_id": user_id,
                "run_id": run_id,
                "job_id": job_id,
                "artifact_id": artifact_id,
                "stage": stage,
                "artifact_type": artifact_type,
                "object_key": object_key,
                "manifest": manifest,
                "created_at": created_at,
            }
        elif statement.startswith("select user_id, run_id, artifact_id") and "where user_id = %s and run_id = %s" in statement:
            assert params is not None
            user_id, run_id = params
            self._rows = [
                (
                    artifact["user_id"],
                    artifact["run_id"],
                    artifact["artifact_id"],
                    artifact["stage"],
                    artifact["artifact_type"],
                    bool(artifact.get("manifest", {}).get("has_markdown", False)),
                    artifact["created_at"],
                )
                for artifact in self.connection.artifacts.values()
                if artifact["user_id"] == user_id and artifact["run_id"] == run_id
            ]
        elif statement.startswith("select user_id, run_id, artifact_id"):
            assert params is not None
            user_id, artifact_id = params
            artifact = self.connection.artifacts.get(artifact_id)
            self._row = (
                (
                    artifact["user_id"],
                    artifact["run_id"],
                    artifact["artifact_id"],
                    artifact["stage"],
                    artifact["artifact_type"],
                    bool(artifact.get("manifest", {}).get("has_markdown", False)),
                    artifact["created_at"],
                )
                if artifact is not None and artifact["user_id"] == user_id
                else None
            )
        elif statement.startswith("insert into artifact_document_chunks"):
            assert params is not None
            artifact_id, chunk_index, user_id, run_id, text_content, token_count, metadata, created_at = params
            self.connection.document_chunks[(artifact_id, chunk_index)] = (
                artifact_id,
                chunk_index,
                user_id,
                run_id,
                text_content,
                token_count,
                metadata,
                created_at,
            )
        elif statement.startswith("select artifact_id, chunk_index"):
            assert params is not None
            (artifact_id,) = params
            self._rows = [
                row
                for (stored_artifact_id, _chunk_index), row in sorted(
                    self.connection.document_chunks.items(),
                    key=lambda item: item[0][1],
                )
                if stored_artifact_id == artifact_id
            ]
        elif statement.startswith("insert into stage_events"):
            assert params is not None
            event_id, user_id, run_id, job_id, stage, event_type, severity, message, payload, created_at = params
            self.connection.stage_events.append(
                (event_id, user_id, run_id, job_id, stage, event_type, severity, message, payload, created_at)
            )
        elif statement.startswith("select stage_event_id"):
            assert params is not None
            user_id, run_id = params
            self._rows = [
                row
                for row in self.connection.stage_events
                if row[1] == user_id and row[2] == run_id
            ]
        elif statement.startswith("insert into provider_config_versions"):
            assert params is not None
            config_version_id, status, provider_name, created_at = params
            self.connection.provider_configs[config_version_id] = (
                config_version_id,
                status,
                provider_name,
                created_at,
            )
        elif statement.startswith("insert into provider_config_entries"):
            assert params is not None
            config_version_id, role, model_id, prompt_version = params
            self.connection.provider_config_entries[(config_version_id, role)] = (
                config_version_id,
                role,
                model_id,
                prompt_version,
            )
        elif statement.startswith("select config_version_id, status, provider_name") and "where status = 'active'" in statement:
            active = [
                row
                for row in self.connection.provider_configs.values()
                if row[1] == "active"
            ]
            self._row = active[-1] if active else None
        elif statement.startswith("select config_version_id, status, provider_name"):
            assert params is not None
            self._row = self.connection.provider_configs.get(params[0])
        elif statement.startswith("select role, model_id, prompt_version"):
            assert params is not None
            (config_version_id,) = params
            self._rows = [
                (role, model_id, prompt_version)
                for stored_config_id, role, model_id, prompt_version in self.connection.provider_config_entries.values()
                if stored_config_id == config_version_id
            ]
        elif statement.startswith("insert into prompt_versions"):
            assert params is not None
            self.connection.prompts[(params[1], params[2])] = params
        elif statement.startswith("select prompt_version_id"):
            assert params is not None
            prompt_id, prompt_version = params
            self._row = self.connection.prompts.get((prompt_id, prompt_version))
        elif statement.startswith("insert into provider_runs"):
            assert params is not None
            provider_run_id, user_id, run_id, job_id, provider_name, model_id, started_at, raw_usage = params
            self.connection.provider_runs[provider_run_id] = (
                provider_run_id,
                user_id,
                run_id,
                job_id,
                provider_name,
                model_id,
                started_at,
                raw_usage,
            )
        elif statement.startswith("insert into usage_ledger"):
            assert params is not None
            usage_ledger_id, provider_run_id, user_id, run_id, job_id, provider_name, model_id, cost_usd, usage, created_at = params
            self.connection.usage_ledger[provider_run_id] = (
                usage_ledger_id,
                provider_run_id,
                user_id,
                run_id,
                job_id,
                provider_name,
                model_id,
                float(cost_usd),
                usage,
                created_at,
            )
        elif statement.startswith("update usage_ledger"):
            assert params is not None
            usage, provider_run_id = params
            row = self.connection.usage_ledger.get(provider_run_id)
            if row is not None:
                self.connection.usage_ledger[provider_run_id] = (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    0.0,
                    usage,
                    row[9],
                )
        elif statement.startswith("select provider_run_id, user_id, run_id"):
            assert params is not None
            user_id, run_id = params
            self._rows = [
                row[1:]
                for row in self.connection.usage_ledger.values()
                if row[2] == user_id and row[3] == run_id
            ]
        elif statement.startswith("select coalesce(sum(cost_usd)"):
            assert params is not None
            user_id, run_id = params
            reservation_only = "usage ->> 'kind' = 'reservation'" in statement
            total = 0.0
            for row in self.connection.usage_ledger.values():
                if row[2] != user_id or row[3] != run_id:
                    continue
                usage = row[8]
                if reservation_only and usage.get("kind") != "reservation":
                    continue
                if not reservation_only and usage.get("kind") == "reservation":
                    continue
                total += row[7]
            self._row = (total,)
        elif statement.startswith("select scope_name, cache_key"):
            assert params is not None
            user_id, scope_name, cache_key = params
            entry = self.connection.cache_entries.get((user_id, scope_name, cache_key))
            self._row = entry
        elif statement.startswith("insert into cache_entries"):
            assert params is not None
            user_id, scope_name, cache_key, payload, created_at = params
            self.connection.cache_entries[(user_id, scope_name, cache_key)] = (
                scope_name,
                cache_key,
                payload,
                created_at,
            )
        elif statement.startswith("delete from cache_entries"):
            assert params is not None
            user_id, scope_name, cache_key = params
            deleted = self.connection.cache_entries.pop((user_id, scope_name, cache_key), None)
            self.rowcount = 1 if deleted is not None else 0
        elif statement.startswith("insert into cleanup_tasks"):
            assert params is not None
            (
                cleanup_task_id,
                user_id,
                run_id,
                task_type,
                status,
                run_at,
                attempts,
                max_attempts,
                payload,
                created_at,
                updated_at,
                completed_at,
                last_error,
            ) = params
            self.connection.cleanup_tasks.setdefault(
                cleanup_task_id,
                (
                    cleanup_task_id,
                    user_id,
                    run_id,
                    task_type,
                    status,
                    run_at,
                    attempts,
                    max_attempts,
                    payload,
                    created_at,
                    updated_at,
                    completed_at,
                    last_error,
                ),
            )
        elif statement.startswith("select cleanup_task_id"):
            assert params is not None
            self._row = self.connection.cleanup_tasks.get(params[0])
        elif (
            statement.startswith("select user_id, run_id")
            and "order by created_at, run_id" in statement
        ):
            assert params is not None
            (user_id,) = params
            self._rows = [
                row for row in self.connection.runs.values() if row[0] == user_id
            ]
        elif statement.startswith("select user_id, run_id") and "where user_id = %s and run_id = %s" in statement:
            assert params is not None
            user_id, run_id = params
            row = self.connection.runs.get(run_id)
            self._row = row if row is not None and row[0] == user_id else None
        elif statement.startswith("update runs"):
            assert params is not None
            (
                status,
                upload_id,
                description,
                provider_config_version_id,
                _running_status,
                _started_at,
                _terminal_status,
                _completed_at,
                _cancel_status,
                _cancelled_at,
                user_id,
                run_id,
            ) = params
            row = self.connection.runs.get(run_id)
            if row is not None and row[0] == user_id:
                self.connection.runs[run_id] = (
                    user_id,
                    run_id,
                    status,
                    upload_id,
                    description,
                    provider_config_version_id,
                    row[-1],
                )
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def _update_terminal_job(
        self,
        job_id: str,
        lease_id: str,
        status: str,
        completed_at: datetime,
    ) -> None:
        job = self.connection.jobs.get(job_id)
        if job is None or job["lease_id"] != lease_id:
            self._row = None
            return
        job["status"] = status
        job["lease_owner_service_identity_id"] = None
        job["lease_id"] = None
        job["lease_expires_at"] = None
        job["completed_at"] = completed_at
        self._row = (job["job_id"],)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    def __init__(self) -> None:
        self.users: dict[str, tuple[Any, ...]] = {}
        self.service_identities: dict[str, tuple[Any, ...]] = {}
        self.sessions: dict[str, tuple[Any, ...]] = {}
        self.sessions_by_hash: dict[str, str] = {}
        self.api_tokens: dict[str, tuple[Any, ...]] = {}
        self.api_tokens_by_hash: dict[str, str] = {}
        self.uploads: dict[str, tuple[Any, ...]] = {}
        self.runs: dict[str, tuple[Any, ...]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.attempts_by_job: dict[str, int] = {}
        self.job_attempts: dict[tuple[str, str], dict[str, Any]] = {}
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.document_chunks: dict[tuple[str, int], tuple[Any, ...]] = {}
        self.stage_events: list[tuple[Any, ...]] = []
        self.provider_configs: dict[str, tuple[Any, ...]] = {}
        self.provider_config_entries: dict[tuple[str, str], tuple[Any, ...]] = {}
        self.prompts: dict[tuple[str, str], tuple[Any, ...]] = {}
        self.provider_runs: dict[str, tuple[Any, ...]] = {}
        self.usage_ledger: dict[str, tuple[Any, ...]] = {}
        self.cache_entries: dict[tuple[str, str, str], tuple[Any, ...]] = {}
        self.cleanup_tasks: dict[str, tuple[Any, ...]] = {}
        self.executed: list[tuple[str, tuple[Any, ...] | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on_table: str | None = None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def maybe_fail(self, table_name: str) -> None:
        if self.fail_on_table == table_name:
            raise RuntimeError(f"forced failure on {table_name}")


def test_postgres_metadata_store_creates_and_reads_user_and_service_identity():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    user = UserRecord(
        user_id=UserId.new(),
        username="madeel",
        status=UserStatus.ACTIVE,
        created_at=_utc(2026, 1, 1),
    )
    service = ServiceIdentityRecord(
        service_identity_id=ServiceIdentityId.new(),
        name="processor",
        status=ServiceIdentityStatus.ACTIVE,
        created_at=_utc(2026, 1, 2),
    )

    store.create_user(user)
    store.create_service_identity(service)

    assert store.get_user(user.user_id) == user
    assert store.get_service_identity(service.service_identity_id) == service
    assert connection.commits == 2
    assert connection.rollbacks == 0


def test_postgres_metadata_store_supports_runtime_auth_and_lifecycle_updates():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    user = UserRecord(
        user_id=UserId.new(),
        username="local_user",
        status=UserStatus.ACTIVE,
        password_hash="HASHED:local_password",
        created_at=_utc(2026, 1, 2),
    )
    store.create_user(user)

    assert store.get_user(user.user_id) == user
    assert store.get_user_by_username("local_user") == user

    disabled = UserRecord(
        user_id=user.user_id,
        username=user.username,
        status=UserStatus.DISABLED,
        password_hash="HASHED:new_password",
        created_at=user.created_at,
    )
    store.update_user(disabled)
    assert store.get_user(user.user_id) == disabled
    assert store.list_users() == [disabled]

    session = SessionRecord(
        session_id=SessionId.new(),
        user_id=user.user_id,
        token_hash="session-token-hash",
        expires_at=_utc(2026, 1, 9),
        created_at=_utc(2026, 1, 2),
    )
    store.create_session(session)
    assert store.get_session_by_token_hash("session-token-hash") == session
    store.revoke_session(session.session_id, revoked_at=_utc(2026, 1, 8))
    revoked = store.get_session_by_token_hash("session-token-hash")
    assert revoked is not None
    assert revoked.revoked_at == _utc(2026, 1, 8)

    token = BearerTokenRecord(
        token_id=TokenId.new(),
        user_id=user.user_id,
        token_hash="bearer-token-hash",
        name="local automation",
        description="local compose token",
        created_at=_utc(2026, 1, 2),
        expires_at=_utc(2026, 1, 10),
    )
    store.create_bearer_token(token)
    assert store.get_bearer_token(token.token_id) == token
    assert store.get_bearer_token_by_hash("bearer-token-hash") == token
    assert store.list_bearer_tokens_for_user(user.user_id) == [token]

    upload = UploadRecord(
        user_id=user.user_id,
        upload_id=UploadId.new(),
        status=UploadStatus.INITIALIZED,
        original_filename="sample.wav",
        content_type="audio/wav",
        size_bytes=44,
        created_at=_utc(2026, 1, 3),
    )
    store.create_upload(upload)
    completed_upload = UploadRecord(
        user_id=user.user_id,
        upload_id=upload.upload_id,
        status=UploadStatus.COMPLETED,
        original_filename=upload.original_filename,
        content_type=upload.content_type,
        size_bytes=upload.size_bytes,
        created_at=upload.created_at,
    )
    store.update_upload(completed_upload)
    assert store.get_upload(
        user_id=user.user_id,
        upload_id=upload.upload_id,
    ) == completed_upload

    run = RunRecord(
        user_id=user.user_id,
        run_id=RunId.new(),
        status=RunStatus.PENDING,
        upload_id=upload.upload_id,
        description="compose fake-provider run",
        created_at=_utc(2026, 1, 4),
    )
    store.create_run(run)
    running = RunRecord(
        user_id=user.user_id,
        run_id=run.run_id,
        status=RunStatus.RUNNING,
        upload_id=run.upload_id,
        description=run.description,
        created_at=run.created_at,
    )
    store.update_run(running)
    assert store.get_run(user_id=user.user_id, run_id=run.run_id) == running

    job = JobRecord(
        user_id=user.user_id,
        run_id=run.run_id,
        job_id=JobId.new(),
        status=JobStatus.RUNNING,
        created_at=_utc(2026, 1, 5),
    )
    store.create_job(job, stage=ArtifactStage.CHUNKER)
    succeeded_job = JobRecord(
        user_id=user.user_id,
        run_id=run.run_id,
        job_id=job.job_id,
        status=JobStatus.SUCCEEDED,
        created_at=job.created_at,
    )
    store.update_job(succeeded_job)
    assert store.get_job_by_id(job.job_id) == succeeded_job

    artifact = ArtifactRecord(
        user_id=user.user_id,
        run_id=run.run_id,
        artifact_id=ArtifactId.new(),
        stage=ArtifactStage.ARTICLE_GENERATOR,
        artifact_type=ArtifactType.FINAL_ARTICLE,
        has_markdown=True,
        created_at=_utc(2026, 1, 6),
    )
    store.record_artifact(
        artifact,
        job_id=job.job_id,
        object_key=f"artifacts/{artifact.artifact_id}.json",
    )
    assert store.list_run_artifacts(user_id=user.user_id, run_id=run.run_id) == [artifact]
    assert store.get_artifact(
        user_id=user.user_id,
        artifact_id=artifact.artifact_id,
    ) == artifact


def test_postgres_metadata_store_enforces_upload_ownership_and_lists_by_user():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = UserRecord(UserId.new(), "owner", UserStatus.ACTIVE)
    other = UserRecord(UserId.new(), "other", UserStatus.ACTIVE)
    store.create_user(owner)
    store.create_user(other)
    owner_upload = UploadRecord(
        user_id=owner.user_id,
        upload_id=UploadId.new(),
        status=UploadStatus.COMPLETED,
        original_filename="owner.mp3",
        created_at=_utc(2026, 1, 3),
    )
    other_upload = UploadRecord(
        user_id=other.user_id,
        upload_id=UploadId.new(),
        status=UploadStatus.COMPLETED,
        original_filename="other.mp3",
        created_at=_utc(2026, 1, 4),
    )

    store.create_upload(owner_upload)
    store.create_upload(other_upload)

    assert store.get_upload(user_id=owner.user_id, upload_id=owner_upload.upload_id) == owner_upload
    assert store.get_upload(user_id=owner.user_id, upload_id=other_upload.upload_id) is None
    assert store.list_uploads(user_id=owner.user_id) == [owner_upload]


def test_postgres_metadata_store_enforces_run_ownership_and_lists_by_user():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = UserRecord(UserId.new(), "owner", UserStatus.ACTIVE)
    other = UserRecord(UserId.new(), "other", UserStatus.ACTIVE)
    store.create_user(owner)
    store.create_user(other)
    owner_run = RunRecord(
        user_id=owner.user_id,
        run_id=RunId.new(),
        status=RunStatus.RUNNING,
        created_at=_utc(2026, 1, 5),
    )
    other_run = RunRecord(
        user_id=other.user_id,
        run_id=RunId.new(),
        status=RunStatus.PENDING,
        created_at=_utc(2026, 1, 6),
    )

    store.create_run(owner_run)
    store.create_run(other_run)

    assert store.get_run(user_id=owner.user_id, run_id=owner_run.run_id) == owner_run
    assert store.get_run(user_id=owner.user_id, run_id=other_run.run_id) is None
    assert store.list_runs(user_id=owner.user_id) == [owner_run]


def test_postgres_metadata_store_rolls_back_failed_create():
    connection = FakeConnection()
    connection.fail_on_table = "users"
    store = PostgresMetadataStore(connection)
    user = UserRecord(UserId.new(), "rollback", UserStatus.ACTIVE)

    with pytest.raises(RuntimeError):
        store.create_user(user)

    assert store.get_user(user.user_id) is None
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_postgres_metadata_store_creates_and_reads_user_owned_job():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    other = UserRecord(UserId.new(), "other", UserStatus.ACTIVE)
    store.create_user(other)
    job = JobRecord(
        user_id=owner.user_id,
        run_id=owner.run_id,
        job_id=JobId.new(),
        status=JobStatus.QUEUED,
        created_at=_utc(2026, 1, 7),
    )

    store.create_job(job, stage=ArtifactStage.TRANSLATOR)

    assert store.get_job(user_id=owner.user_id, job_id=job.job_id) == job
    assert store.get_job(user_id=other.user_id, job_id=job.job_id) is None
    assert connection.jobs[str(job.job_id)]["stage"] == "translator"


def test_postgres_metadata_store_claims_job_with_compare_and_set():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    job = _create_job(store, owner)
    worker = _create_service(store)
    now = _utc(2026, 1, 8)

    first = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    second = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now + timedelta(seconds=1),
    )

    assert first is not None
    assert first.job_id == job.job_id
    assert first.attempt_number == 1
    assert first.expires_at == now + timedelta(seconds=30)
    assert second is None
    assert store.get_job(user_id=owner.user_id, job_id=job.job_id).status == JobStatus.CLAIMED


def test_postgres_metadata_store_extends_and_expires_job_lease():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    job = _create_job(store, owner)
    worker = _create_service(store)
    now = _utc(2026, 1, 9)
    lease = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    assert lease is not None

    extended = store.extend_job_lease(
        lease,
        lease_seconds=90,
        now=now + timedelta(seconds=10),
    )
    blocked = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now + timedelta(seconds=60),
    )
    reclaimed = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now + timedelta(seconds=120),
    )

    assert extended.expires_at == now + timedelta(seconds=100)
    assert blocked is None
    assert reclaimed is not None
    assert reclaimed.lease_id != lease.lease_id
    assert reclaimed.attempt_number == 2


def test_postgres_metadata_store_retries_job_after_active_lease():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    job = _create_job(store, owner)
    worker = _create_service(store)
    now = _utc(2026, 1, 10)
    lease = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    assert lease is not None

    store.retry_job(lease, reason="temporary provider outage", now=now)
    retried = store.claim_job(
        job_id=job.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now + timedelta(seconds=1),
    )

    assert store.get_job(user_id=owner.user_id, job_id=job.job_id).status == JobStatus.CLAIMED
    assert retried is not None
    assert retried.attempt_number == 2


def test_postgres_metadata_store_cancels_terminal_fails_and_dead_letters_jobs():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner = _create_user_and_run(store)
    worker = _create_service(store)
    now = _utc(2026, 1, 11)

    cancelled = _create_job(store, owner)
    store.cancel_job(cancelled.job_id, reason="user requested cancellation", now=now)
    assert store.get_job(user_id=owner.user_id, job_id=cancelled.job_id).status == JobStatus.CANCELLED
    assert store.claim_job(
        job_id=cancelled.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    ) is None

    failed = _create_job(store, owner)
    failed_lease = store.claim_job(
        job_id=failed.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    assert failed_lease is not None
    store.mark_job_terminal_failure(failed_lease, reason="retry limit exceeded", now=now)
    assert store.get_job(user_id=owner.user_id, job_id=failed.job_id).status == JobStatus.FAILED

    dead_lettered = _create_job(store, owner)
    dead_letter_lease = store.claim_job(
        job_id=dead_lettered.job_id,
        worker_id=worker.service_identity_id,
        lease_seconds=30,
        now=now,
    )
    assert dead_letter_lease is not None
    store.dead_letter_job(dead_letter_lease, reason="poison message", now=now)
    assert store.get_job(
        user_id=owner.user_id,
        job_id=dead_lettered.job_id,
    ).status == JobStatus.DEAD_LETTERED


def test_postgres_metadata_store_round_trips_artifact_document_chunks_below_256kb():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    run = _create_user_and_run(store)
    job = _create_job(store, run)
    artifact = ArtifactRecord(
        user_id=run.user_id,
        run_id=run.run_id,
        artifact_id=ArtifactId.new(),
        stage=ArtifactStage.TRANSLATOR,
        artifact_type=ArtifactType.ENGLISH_TRANSLATION,
        created_at=_utc(2026, 1, 12),
    )
    chunk = ArtifactDocumentChunkRecord(
        artifact_id=artifact.artifact_id,
        chunk_index=0,
        user_id=run.user_id,
        run_id=run.run_id,
        text_content="x" * (256 * 1024 - 1),
        token_count=123,
        metadata={"language": "en"},
        created_at=_utc(2026, 1, 12),
    )
    event = StageEventRecord(
        user_id=run.user_id,
        run_id=run.run_id,
        job_id=job.job_id,
        stage=ArtifactStage.TRANSLATOR,
        event_type="artifact_written",
        severity="info",
        message="wrote chunk",
        payload={"artifact_id": str(artifact.artifact_id)},
        created_at=_utc(2026, 1, 12),
    )

    store.record_artifact(artifact, job_id=job.job_id, object_key="runs/r/artifact.json")
    store.put_artifact_document_chunk(chunk)
    store.record_stage_event(event)

    assert store.get_artifact(user_id=run.user_id, artifact_id=artifact.artifact_id) == artifact
    assert store.list_artifact_document_chunks(artifact_id=artifact.artifact_id) == [chunk]
    assert store.list_stage_events(user_id=run.user_id, run_id=run.run_id) == [event]


def test_postgres_metadata_store_redacts_sensitive_stage_event_fields():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    run = _create_user_and_run(store)
    job = _create_job(store, run)
    event = StageEventRecord(
        user_id=run.user_id,
        run_id=run.run_id,
        job_id=job.job_id,
        stage=ArtifactStage.TRANSLATOR,
        event_type="stage_failed",
        severity="error",
        message="failed while processing NEVER_LOG_TRANSLATION",
        payload={
            "stage": "translator",
            "safe_count": 3,
            "object_key": "artifacts/users/user_abc/runs/run_def/translator/artifact.json",
            "prompt": "NEVER_LOG_PROMPT",
            "full_text_english": "NEVER_LOG_FULL_TRANSLATION",
            "article": {"body_markdown": "NEVER_LOG_ARTICLE_BODY"},
        },
        created_at=_utc(2026, 1, 12),
    )

    store.record_stage_event(event)

    [stored] = store.list_stage_events(user_id=run.user_id, run_id=run.run_id)
    assert stored.message == "stage_failed"
    assert stored.payload["stage"] == "translator"
    assert stored.payload["safe_count"] == 3
    serialized = str(stored.payload) + str(stored.message)
    for forbidden in (
        "NEVER_LOG_TRANSLATION",
        "NEVER_LOG_PROMPT",
        "NEVER_LOG_FULL_TRANSLATION",
        "NEVER_LOG_ARTICLE_BODY",
        "artifacts/users/user_abc",
    ):
        assert forbidden not in serialized


def test_postgres_metadata_store_round_trips_provider_config_and_prompt_versions():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    snapshot = ProviderConfigSnapshot(
        config_version_id=ProviderConfigVersionId.new(),
        status=ProviderConfigStatus.ACTIVE,
        provider_name="fake",
        model_roles={"translation": "fake-text", "article": "fake-article"},
        prompt_versions={"translation": "v1", "article": "v2"},
    )
    prompt = PromptVersionRecord(
        prompt_version_id="prompt-version-1",
        prompt_id="translation",
        prompt_version="v1",
        stage_name=ArtifactStage.TRANSLATOR,
        body="Translate safely.",
        checksum_sha256="abc123",
        is_active=True,
        created_at=_utc(2026, 1, 13),
    )

    store.save_provider_config(snapshot)
    store.upsert_prompt_version(prompt)

    assert store.get_active_config() == snapshot
    assert store.get_config(snapshot.config_version_id) == snapshot
    assert store.model_for_role(snapshot.config_version_id, "article") == "fake-article"
    assert store.get_prompt_version(prompt_id="translation", prompt_version="v1") == prompt


def test_postgres_metadata_store_usage_reservations_cache_and_actual_costs_survive_restart():
    connection = FakeConnection()
    first_store = PostgresMetadataStore(connection)
    run = _create_user_and_run(first_store)
    job = _create_job(first_store, run)
    reservation = UsageRecord(
        provider_run_id=ProviderRunId.new(),
        user_id=run.user_id,
        run_id=run.run_id,
        job_id=job.job_id,
        provider_name="fake",
        model_id="fake-text",
        cost_usd=0.75,
        usage={"phase": "reserve"},
        created_at=_utc(2026, 1, 14),
    )
    actual = UsageRecord(
        provider_run_id=ProviderRunId.new(),
        user_id=run.user_id,
        run_id=run.run_id,
        job_id=job.job_id,
        provider_name="fake",
        model_id="fake-text",
        cost_usd=0.20,
        usage={"input_tokens": 10, "output_tokens": 20},
        created_at=_utc(2026, 1, 14),
    )
    scope = CacheScope(user_id=run.user_id, name="translator")

    first_store.reserve_usage(reservation)
    first_store.put(scope, "cache-key", {"value": "cached"})

    second_store = PostgresMetadataStore(connection)
    assert second_store.total_reserved_cost_usd(user_id=run.user_id, run_id=run.run_id) == 0.75
    assert dict(second_store.get(scope, "cache-key").payload) == {"value": "cached"}

    second_store.release_usage(reservation.provider_run_id)
    second_store.record_usage(actual)

    third_store = PostgresMetadataStore(connection)
    assert third_store.total_reserved_cost_usd(user_id=run.user_id, run_id=run.run_id) == 0.0
    assert third_store.total_run_cost_usd(user_id=run.user_id, run_id=run.run_id) == 0.20
    assert third_store.list_run_usage(user_id=run.user_id, run_id=run.run_id)[-1] == actual
    assert third_store.delete(scope, "cache-key") is True
    assert third_store.get(scope, "cache-key") is None


def test_postgres_metadata_store_cache_does_not_leak_across_users():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    owner_a = _create_user_and_run(store)
    owner_b = _create_user_and_run(store)
    scope_a = CacheScope(user_id=owner_a.user_id, name="translator")
    scope_b = CacheScope(user_id=owner_b.user_id, name="translator")

    store.put(scope_a, "shared-key", {"value": "user-a-data"})
    store.put(scope_b, "shared-key", {"value": "user-b-data"})

    entry_a = store.get(scope_a, "shared-key")
    entry_b = store.get(scope_b, "shared-key")
    assert entry_a is not None and dict(entry_a.payload) == {"value": "user-a-data"}
    assert entry_b is not None and dict(entry_b.payload) == {"value": "user-b-data"}

    store.delete(scope_a, "shared-key")
    assert store.get(scope_a, "shared-key") is None
    assert store.get(scope_b, "shared-key") is not None, (
        "deleting user A cache must not remove user B cache"
    )


def test_postgres_metadata_store_cleanup_task_creation_is_idempotent():
    connection = FakeConnection()
    store = PostgresMetadataStore(connection)
    run = _create_user_and_run(store)
    cleanup = CleanupTaskRecord(
        cleanup_task_id=CleanupTaskId.new(),
        user_id=run.user_id,
        run_id=run.run_id,
        task_type="delete_run_objects",
        status=CleanupTaskStatus.PENDING,
        run_at=_utc(2026, 1, 15),
        payload={"prefix": "tmp/users/usr/runs/run"},
        created_at=_utc(2026, 1, 15),
    )

    assert store.create_cleanup_task_once(cleanup) == cleanup
    assert store.create_cleanup_task_once(cleanup) == cleanup
    assert len(connection.cleanup_tasks) == 1
    assert store.get_cleanup_task(cleanup.cleanup_task_id) == cleanup


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def _create_user_and_run(store: PostgresMetadataStore) -> RunRecord:
    user = UserRecord(UserId.new(), f"user-{UserId.new()}", UserStatus.ACTIVE)
    store.create_user(user)
    run = RunRecord(
        user_id=user.user_id,
        run_id=RunId.new(),
        status=RunStatus.QUEUED,
        created_at=_utc(2026, 1, 6),
    )
    store.create_run(run)
    return run


def _create_service(store: PostgresMetadataStore) -> ServiceIdentityRecord:
    service = ServiceIdentityRecord(
        service_identity_id=ServiceIdentityId.new(),
        name=f"worker-{ServiceIdentityId.new()}",
        status=ServiceIdentityStatus.ACTIVE,
    )
    store.create_service_identity(service)
    return service


def _create_job(store: PostgresMetadataStore, run: RunRecord) -> JobRecord:
    job = JobRecord(
        user_id=run.user_id,
        run_id=run.run_id,
        job_id=JobId.new(),
        status=JobStatus.QUEUED,
        created_at=_utc(2026, 1, 7),
    )
    store.create_job(job, stage=ArtifactStage.CHUNKER)
    return job
