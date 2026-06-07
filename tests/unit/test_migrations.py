"""Migration framework tests that do not require a live database."""

from __future__ import annotations

import pytest

from urdu_pipeline.config.settings import get_settings, reset_settings_cache
from urdu_pipeline.infrastructure.db.migrations import (
    Migration,
    MigrationChecksumMismatch,
    load_migrations,
    run_migrations,
)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self._rows: list[tuple[str, str]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args) -> bool:
        return False

    def execute(self, sql: str, params: tuple[str, ...] | None = None) -> None:
        self.connection.executed.append((sql, params))
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("select version, checksum_sha256"):
            self._rows = [
                (version, checksum)
                for version, checksum in sorted(self.connection.applied.items())
            ]
        elif normalized.startswith("insert into schema_migrations"):
            assert params is not None
            version, _name, checksum = params
            self.connection.applied[version] = checksum

    def fetchall(self) -> list[tuple[str, str]]:
        return self._rows


class FakeConnection:
    def __init__(self) -> None:
        self.applied: dict[str, str] = {}
        self.executed: list[tuple[str, tuple[str, ...] | None]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_load_migrations_includes_framework_bootstrap():
    migrations = load_migrations()

    assert migrations
    assert migrations[0].version == "0001"
    assert migrations[0].name == "migration_framework"
    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in migrations[0].sql


def test_load_migrations_includes_core_user_auth_upload_run_tables():
    migrations = {migration.version: migration for migration in load_migrations()}

    assert "0002" in migrations
    assert migrations["0002"].name == "core_user_auth_upload_run_tables"


def test_core_user_auth_upload_run_migration_creates_required_tables():
    sql = _normalized_migration_sql("0002")

    for table_name in (
        "users",
        "service_identities",
        "sessions",
        "api_tokens",
        "uploads",
        "runs",
    ):
        assert f"create table if not exists {table_name}" in sql


def test_core_user_auth_upload_run_migration_defines_required_indexes():
    sql = _normalized_migration_sql("0002")

    for index_name in (
        "idx_sessions_user_id",
        "idx_sessions_expires_at",
        "idx_api_tokens_user_id",
        "idx_api_tokens_service_identity_id",
        "idx_uploads_user_status",
        "idx_uploads_created_at",
        "idx_runs_user_status",
        "idx_runs_upload_id",
    ):
        assert f"create index if not exists {index_name}" in sql


def test_core_user_auth_upload_run_migration_defines_basic_constraints():
    sql = _normalized_migration_sql("0002")

    for constraint in (
        "users_user_id_format",
        "users_status_check",
        "service_identities_service_identity_id_format",
        "service_identities_status_check",
        "sessions_non_empty_id",
        "api_tokens_principal_owner_check",
        "uploads_upload_id_format",
        "uploads_status_check",
        "runs_run_id_format",
        "runs_status_check",
    ):
        assert f"constraint {constraint}" in sql

    assert "unique (username)" in sql
    assert "unique (name)" in sql
    assert "unique (session_hash)" in sql
    assert "unique (token_hash)" in sql
    assert "foreign key (user_id) references users(user_id)" in sql
    assert (
        "foreign key (service_identity_id) "
        "references service_identities(service_identity_id)"
    ) in sql
    assert "foreign key (upload_id) references uploads(upload_id)" in sql


def test_load_migrations_includes_workflow_metadata_tables():
    migrations = {migration.version: migration for migration in load_migrations()}

    assert "0003" in migrations
    assert migrations["0003"].name == (
        "job_attempt_artifact_event_usage_cache_provider_prompt_cleanup_tables"
    )


def test_workflow_metadata_migration_creates_required_tables():
    sql = _normalized_migration_sql("0003")

    for table_name in (
        "provider_config_versions",
        "provider_config_entries",
        "prompt_versions",
        "jobs",
        "job_attempts",
        "artifacts",
        "artifact_document_chunks",
        "stage_events",
        "provider_runs",
        "usage_ledger",
        "cache_entries",
        "cleanup_tasks",
    ):
        assert f"create table if not exists {table_name}" in sql


def test_workflow_metadata_migration_defines_indexes_and_uniqueness():
    sql = _normalized_migration_sql("0003")

    for index_name in (
        "idx_provider_config_versions_status",
        "idx_prompt_versions_prompt_id_active",
        "idx_jobs_run_status",
        "idx_jobs_user_status",
        "idx_job_attempts_job_status",
        "idx_artifacts_run_stage",
        "idx_artifact_document_chunks_artifact",
        "idx_stage_events_run_created_at",
        "idx_provider_runs_job_id",
        "idx_usage_ledger_run_id",
        "idx_cache_entries_scope",
        "idx_cleanup_tasks_status_run_at",
    ):
        assert f"create index if not exists {index_name}" in sql

    for unique_clause in (
        "unique (config_version_id, role)",
        "unique (prompt_id, prompt_version)",
        "unique (job_id, attempt_number)",
        "unique (artifact_id, chunk_index)",
        "unique (user_id, scope_name, cache_key)",
    ):
        assert unique_clause in sql


def test_workflow_metadata_migration_defines_ownership_and_constraints():
    sql = _normalized_migration_sql("0003")

    for ownership_column in (
        "user_id text not null",
        "run_id text not null",
        "job_id text not null",
    ):
        assert ownership_column in sql

    for constraint in (
        "provider_config_versions_config_version_id_format",
        "provider_config_versions_status_check",
        "jobs_job_id_format",
        "jobs_status_check",
        "job_attempts_status_check",
        "artifacts_artifact_id_format",
        "artifacts_stage_check",
        "artifacts_type_check",
        "provider_runs_provider_run_id_format",
        "usage_ledger_cost_non_negative",
        "cache_entries_non_empty_scope",
        "cleanup_tasks_cleanup_task_id_format",
        "cleanup_tasks_status_check",
    ):
        assert f"constraint {constraint}" in sql

    for foreign_key in (
        "foreign key (user_id) references users(user_id)",
        "foreign key (run_id) references runs(run_id)",
        "foreign key (job_id) references jobs(job_id)",
        "foreign key (artifact_id) references artifacts(artifact_id)",
        "foreign key (provider_run_id) references provider_runs(provider_run_id)",
        "foreign key (config_version_id) references provider_config_versions(config_version_id)",
    ):
        assert foreign_key in sql


def test_run_migrations_applies_and_then_skips_existing_versions():
    migration = Migration(
        version="9999",
        name="test_table",
        sql="CREATE TABLE test_migration_table (id text PRIMARY KEY);",
    )
    connection = FakeConnection()

    first = run_migrations(connection, migrations=[migration])
    second = run_migrations(connection, migrations=[migration])

    assert first.applied_versions == ("9999",)
    assert first.skipped_versions == ()
    assert second.applied_versions == ()
    assert second.skipped_versions == ("9999",)
    assert any(
        sql == "CREATE TABLE test_migration_table (id text PRIMARY KEY);"
        for sql, _params in connection.executed
    )
    assert connection.commits == 2
    assert connection.rollbacks == 0


def test_run_migrations_rejects_checksum_drift():
    migration = Migration(version="9999", name="test_table", sql="SELECT 1;")
    connection = FakeConnection()
    connection.applied["9999"] = "not-the-current-checksum"

    with pytest.raises(MigrationChecksumMismatch):
        run_migrations(connection, migrations=[migration])

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_database_url_is_configurable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:15432/db")
    reset_settings_cache()

    assert get_settings().database_url == "postgresql://user:pass@localhost:15432/db"


def _normalized_migration_sql(version: str) -> str:
    migrations = {migration.version: migration for migration in load_migrations()}
    return " ".join(migrations[version].sql.lower().split())
