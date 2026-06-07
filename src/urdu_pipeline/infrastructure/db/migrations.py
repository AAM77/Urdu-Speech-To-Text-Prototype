"""Small PostgreSQL migration runner.

The project only needs ordered SQL migrations at this stage. The psycopg driver
is imported lazily so core package imports do not require database dependencies.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any, Protocol, Sequence

_MIGRATION_PACKAGE = "urdu_pipeline.infrastructure.db.migration_files"
_MIGRATION_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

_ENSURE_MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    name text NOT NULL,
    checksum_sha256 text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


class _Cursor(Protocol):
    def execute(self, sql: str, params: tuple[str, ...] | None = None) -> Any: ...

    def fetchall(self) -> Sequence[tuple[str, str]]: ...


class _Connection(Protocol):
    def cursor(self) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class MigrationError(RuntimeError):
    """Base migration error."""


class MigrationChecksumMismatch(MigrationError):
    """Raised when an applied migration no longer matches its SQL file."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str

    @property
    def checksum_sha256(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MigrationReport:
    applied_versions: tuple[str, ...]
    skipped_versions: tuple[str, ...]


def load_migrations(package: str = _MIGRATION_PACKAGE) -> tuple[Migration, ...]:
    """Load migration files from package data in filename order."""
    migrations: list[Migration] = []
    seen_versions: set[str] = set()
    for entry in sorted(resources.files(package).iterdir(), key=lambda item: item.name):
        match = _MIGRATION_RE.fullmatch(entry.name)
        if match is None:
            continue
        version = match.group("version")
        if version in seen_versions:
            raise MigrationError(f"duplicate migration version: {version}")
        seen_versions.add(version)
        migrations.append(
            Migration(
                version=version,
                name=match.group("name"),
                sql=entry.read_text(encoding="utf-8"),
            )
        )
    return tuple(migrations)


def connect_postgres(database_url: str) -> Any:
    """Connect to PostgreSQL using the optional psycopg dependency."""
    try:
        psycopg = importlib.import_module("psycopg")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "psycopg is required for database migrations. "
            "Install the `db` extra, for example: pip install -e '.[db]'."
        ) from exc
    return psycopg.connect(database_url)


def migrate_database(database_url: str | None = None) -> MigrationReport:
    """Connect to the configured database and apply pending migrations."""
    if database_url is None:
        from urdu_pipeline.config.settings import get_settings

        database_url = get_settings().database_url

    connection = connect_postgres(database_url)
    try:
        return run_migrations(connection)
    finally:
        connection.close()


def run_migrations(
    connection: _Connection,
    *,
    migrations: Sequence[Migration] | None = None,
) -> MigrationReport:
    """Apply all pending migrations in one transaction."""
    pending = tuple(migrations) if migrations is not None else load_migrations()
    applied_versions: list[str] = []
    skipped_versions: list[str] = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(_ENSURE_MIGRATION_TABLE_SQL)
            applied = _load_applied_versions(cursor)
            for migration in pending:
                current_checksum = migration.checksum_sha256
                previous_checksum = applied.get(migration.version)
                if previous_checksum is not None:
                    if previous_checksum != current_checksum:
                        raise MigrationChecksumMismatch(
                            f"migration {migration.version} checksum mismatch"
                        )
                    skipped_versions.append(migration.version)
                    continue

                cursor.execute(migration.sql)
                cursor.execute(
                    (
                        "INSERT INTO schema_migrations "
                        "(version, name, checksum_sha256) VALUES (%s, %s, %s)"
                    ),
                    (migration.version, migration.name, current_checksum),
                )
                applied_versions.append(migration.version)
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return MigrationReport(
        applied_versions=tuple(applied_versions),
        skipped_versions=tuple(skipped_versions),
    )


def _load_applied_versions(cursor: _Cursor) -> dict[str, str]:
    cursor.execute(
        "SELECT version, checksum_sha256 FROM schema_migrations ORDER BY version"
    )
    return {version: checksum for version, checksum in cursor.fetchall()}
