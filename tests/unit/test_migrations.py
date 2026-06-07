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
