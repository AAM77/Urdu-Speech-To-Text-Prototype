"""Metadata, queue, cache, auth, secret, provider, usage, and budget port tests."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


def test_service_port_modules_import_without_optional_adapter_dependencies():
    blocked_roots = [
        "boto3",
        "botocore",
        "fastapi",
        "minio",
        "openai",
        "redis",
        "sqlalchemy",
        "streamlit",
        "typer",
        "uvicorn",
    ]
    script = f"""
import importlib
import importlib.abc
import sys

blocked_roots = {blocked_roots!r}


class OptionalDependencyBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".", 1)[0]
        if root in blocked_roots:
            raise AssertionError(f"optional dependency imported during port import: {{fullname}}")
        return None


sys.meta_path.insert(0, OptionalDependencyBlocker())
importlib.import_module("urdu_pipeline.application.ports.services")
importlib.import_module("urdu_pipeline.application.ports")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr


def test_job_queue_port_includes_required_lifecycle_methods_and_safe_payload_shape():
    from urdu_pipeline.application.ports.services import (
        JobLease,
        JobQueue,
        QueueMessage,
    )
    from urdu_pipeline.domain import JobId, ServiceIdentityId

    assert {field.name for field in fields(QueueMessage)} == {"job_id", "routing"}

    message = QueueMessage(
        job_id=JobId.new(),
        routing={"queue": "default", "stage": "transcriber", "priority": "normal"},
    )
    assert message.job_id.startswith("job_")
    assert "user_id" not in message.routing
    assert "object_key" not in message.routing

    required_methods = {
        "enqueue",
        "claim",
        "extend_lease",
        "retry",
        "mark_terminal_failure",
        "cancel",
        "dead_letter",
    }
    assert required_methods.issubset(JobQueue.__dict__)

    class FakeJobQueue:
        def __init__(self) -> None:
            self.messages: list[QueueMessage] = []

        def enqueue(self, message: QueueMessage) -> None:
            self.messages.append(message)

        def claim(
            self,
            *,
            worker_id: ServiceIdentityId,
            lease_seconds: int,
        ) -> JobLease | None:
            if not self.messages:
                return None
            queued = self.messages.pop(0)
            return JobLease(
                job_id=queued.job_id,
                lease_id="lease-1",
                attempt_number=1,
                expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=lease_seconds),
                routing=queued.routing,
            )

        def extend_lease(
            self,
            lease: JobLease,
            *,
            lease_seconds: int,
        ) -> JobLease:
            return JobLease(
                job_id=lease.job_id,
                lease_id=lease.lease_id,
                attempt_number=lease.attempt_number,
                expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=lease_seconds),
                routing=lease.routing,
            )

        def retry(self, lease: JobLease, *, reason: str) -> None:
            return None

        def mark_terminal_failure(self, lease: JobLease, *, reason: str) -> None:
            return None

        def cancel(self, job_id: JobId, *, reason: str) -> None:
            return None

        def dead_letter(self, lease: JobLease, *, reason: str) -> None:
            return None

    queue = FakeJobQueue()
    assert isinstance(queue, JobQueue)
    queue.enqueue(message)
    lease = queue.claim(worker_id=ServiceIdentityId.new(), lease_seconds=30)
    assert lease is not None
    assert lease.job_id == message.job_id
    assert queue.extend_lease(lease, lease_seconds=60).expires_at > lease.expires_at


def test_metadata_cache_auth_secret_provider_usage_and_budget_ports_are_structural():
    from urdu_pipeline.application.ports.services import (
        AuthPrincipal,
        AuthService,
        BudgetDecision,
        BudgetService,
        CacheEntry,
        CacheScope,
        CacheStore,
        ProviderConfigSnapshot,
        ProviderRegistry,
        SecretProvider,
        SecretValue,
        UsageLedger,
        UsageRecord,
    )
    from urdu_pipeline.domain import (
        JobId,
        ProviderConfigStatus,
        ProviderConfigVersionId,
        ProviderRunId,
        RunId,
        ServiceIdentityId,
        UserId,
    )

    class FakeCacheStore:
        def __init__(self) -> None:
            self.entries: dict[tuple[CacheScope, str], CacheEntry] = {}

        def get(self, scope: CacheScope, key: str) -> CacheEntry | None:
            return self.entries.get((scope, key))

        def put(
            self,
            scope: CacheScope,
            key: str,
            payload: Mapping[str, Any],
        ) -> CacheEntry:
            entry = CacheEntry(scope=scope, key=key, payload=payload)
            self.entries[(scope, key)] = entry
            return entry

        def delete(self, scope: CacheScope, key: str) -> bool:
            return self.entries.pop((scope, key), None) is not None

    class FakeAuthService:
        def authenticate_password(self, username: str, password: str) -> AuthPrincipal | None:
            return AuthPrincipal(principal_id=UserId.new(), kind="user", scopes=frozenset({"runs:read"}))

        def authenticate_bearer_token(self, token: str) -> AuthPrincipal | None:
            return AuthPrincipal(principal_id=UserId.new(), kind="user", scopes=frozenset({"runs:write"}))

        def authenticate_service_token(self, token: str) -> AuthPrincipal | None:
            return AuthPrincipal(
                principal_id=ServiceIdentityId.new(),
                kind="service",
                scopes=frozenset({"processor:claim"}),
            )

        def hash_secret(self, secret: str) -> str:
            return f"hashed:{secret}"

        def verify_secret(self, secret: str, secret_hash: str) -> bool:
            return secret_hash == self.hash_secret(secret)

    class FakeSecretProvider:
        def get_secret(self, name: str) -> SecretValue:
            return SecretValue(name=name, value="secret-value")

    class FakeProviderRegistry:
        def __init__(self) -> None:
            self.config = ProviderConfigSnapshot(
                config_version_id=ProviderConfigVersionId.new(),
                status=ProviderConfigStatus.ACTIVE,
                provider_name="fake",
                model_roles={"translation": "fake-text"},
                prompt_versions={"translation": "v1"},
            )

        def get_active_config(self) -> ProviderConfigSnapshot:
            return self.config

        def get_config(
            self,
            config_version_id: ProviderConfigVersionId,
        ) -> ProviderConfigSnapshot | None:
            if config_version_id == self.config.config_version_id:
                return self.config
            return None

        def model_for_role(
            self,
            config_version_id: ProviderConfigVersionId,
            role: str,
        ) -> str:
            return self.config.model_roles[role]

    class FakeUsageLedger:
        def __init__(self) -> None:
            self.records: list[UsageRecord] = []

        def record_usage(self, record: UsageRecord) -> None:
            self.records.append(record)

        def list_run_usage(
            self,
            *,
            user_id: UserId,
            run_id: RunId,
        ) -> Sequence[UsageRecord]:
            return [r for r in self.records if r.user_id == user_id and r.run_id == run_id]

        def total_run_cost_usd(
            self,
            *,
            user_id: UserId,
            run_id: RunId,
        ) -> float:
            return sum(r.cost_usd for r in self.list_run_usage(user_id=user_id, run_id=run_id))

    class FakeBudgetService:
        def check_run_budget(
            self,
            *,
            user_id: UserId,
            run_id: RunId,
            next_cost_usd: float,
        ) -> BudgetDecision:
            return BudgetDecision(
                allowed=True,
                blocked=False,
                warning=False,
                projected_total_usd=next_cost_usd,
                hard_cap_usd=60.0,
                reason="OK",
            )

        def record_actual_cost(
            self,
            *,
            user_id: UserId,
            run_id: RunId,
            cost_usd: float,
        ) -> None:
            return None

    cache = FakeCacheStore()
    auth = FakeAuthService()
    secrets = FakeSecretProvider()
    registry = FakeProviderRegistry()
    usage = FakeUsageLedger()
    budget = FakeBudgetService()

    assert isinstance(cache, CacheStore)
    assert isinstance(auth, AuthService)
    assert isinstance(secrets, SecretProvider)
    assert isinstance(registry, ProviderRegistry)
    assert isinstance(usage, UsageLedger)
    assert isinstance(budget, BudgetService)

    user_id = UserId.new()
    run_id = RunId.new()
    cache_scope = CacheScope(user_id=user_id, name="translation")
    assert cache.put(cache_scope, "cache-key", {"value": "cached"}).payload["value"] == "cached"
    assert auth.authenticate_service_token("token").kind == "service"
    assert secrets.get_secret("OPENAI_API_KEY").value == "secret-value"
    config = registry.get_active_config()
    assert registry.model_for_role(config.config_version_id, "translation") == "fake-text"
    record = UsageRecord(
        provider_run_id=ProviderRunId.new(),
        user_id=user_id,
        run_id=run_id,
        job_id=JobId.new(),
        provider_name="fake",
        model_id="fake-text",
        cost_usd=0.25,
        usage={"input_tokens": 10},
    )
    usage.record_usage(record)
    assert usage.total_run_cost_usd(user_id=user_id, run_id=run_id) == 0.25
    assert budget.check_run_budget(user_id=user_id, run_id=run_id, next_cost_usd=1.0).allowed
