"""Optional PostgreSQL smoke checks for the migration runner."""

from __future__ import annotations

import importlib.util
import os
import uuid

import pytest

from urdu_pipeline.infrastructure.db.migrations import connect_postgres, run_migrations


def test_migrations_can_run_against_configured_postgres_database():
    if os.environ.get("RUN_POSTGRES_MIGRATION_SMOKE") != "1":
        pytest.skip("set RUN_POSTGRES_MIGRATION_SMOKE=1 to run the PostgreSQL smoke test")
    if importlib.util.find_spec("psycopg") is None:
        pytest.skip("psycopg is not installed")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is not configured")

    schema_name = f"migration_smoke_{uuid.uuid4().hex}"
    connection = connect_postgres(database_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA "{schema_name}"')
            cursor.execute(f'SET search_path TO "{schema_name}"')

        report = run_migrations(connection)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
            versions = [row[0] for row in cursor.fetchall()]
        assert versions
        assert versions == list(report.applied_versions)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        connection.commit()
        connection.close()
