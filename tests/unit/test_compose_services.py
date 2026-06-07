"""Docker Compose service wiring tests for the local parity stack."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_FILE = REPO_ROOT / ".env.local.example"


def _compose_config(*, profile: str | None = None) -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")

    cmd = ["docker", "compose", "--env-file", str(ENV_FILE)]
    if profile is not None:
        cmd.extend(["--profile", profile])
    cmd.extend(["-f", str(COMPOSE_FILE), "config", "--format", "json"])

    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=_compose_env(),
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _compose_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in {
        "API_PORT",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "CACHE_ROOT",
        "COMPOSE_PROJECT_NAME",
        "DATABASE_URL",
        "LOG_LEVEL",
        "MINIO_CONSOLE_PORT",
        "MINIO_ENDPOINT",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ROOT_USER",
        "OBJECT_STORE_ACCESS_KEY",
        "OBJECT_STORE_BUCKET",
        "OBJECT_STORE_ENDPOINT",
        "OBJECT_STORE_ENDPOINT_URL",
        "OBJECT_STORE_REGION",
        "OBJECT_STORE_SECRET_KEY",
        "OUTPUT_ROOT",
        "PIPELINE_PROVIDER_MODE",
        "POSTGRES_DB",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "PROCESSOR_API_URL",
        "PROMPT_VERSION",
        "REDIS_PORT",
        "REDIS_URL",
        "REVERSE_PROXY_PORT",
        "SERVICE_AUTH_TOKEN",
    }:
        env.pop(key, None)
    return env


def _command_text(service: dict[str, Any]) -> str:
    command = service.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command or "")


def _healthcheck_text(service: dict[str, Any]) -> str:
    test = service["healthcheck"]["test"]
    if isinstance(test, list):
        return " ".join(str(part) for part in test)
    return str(test)


def _depends_on_healthy(service: dict[str, Any], dependency: str) -> bool:
    depends_on = service.get("depends_on") or {}
    return depends_on.get(dependency, {}).get("condition") == "service_healthy"


def test_compose_config_validates_and_declares_core_services_and_volumes():
    config = _compose_config()

    assert {"api", "processor", "postgres", "minio", "redis"}.issubset(
        config["services"]
    )
    assert {"postgres_data", "minio_data", "redis_data"}.issubset(
        config["volumes"]
    )


def test_api_service_builds_from_api_dockerfile_and_uses_real_health_route():
    config = _compose_config()
    api = config["services"]["api"]

    assert Path(api["build"]["context"]) == REPO_ROOT
    assert api["build"]["dockerfile"] == "Dockerfile.api"
    assert api["image"] == "urdu-pipeline-api:local"
    assert "http.server" not in _command_text(api)
    assert "uvicorn" in _command_text(api)
    assert "urdu_pipeline.api.runtime:create_runtime_app" in _command_text(api)

    healthcheck = _healthcheck_text(api)
    assert "127.0.0.1:8000/health" in healthcheck

    environment = api["environment"]
    assert environment["OUTPUT_ROOT"] == "/app/runs"
    assert environment["CACHE_ROOT"] == "/app/.cache_pipeline"
    assert environment["DATABASE_URL"].endswith("@postgres:5432/urdu_pipeline")
    assert environment["OBJECT_STORE_ENDPOINT_URL"] == "http://minio:9000"
    assert environment["OBJECT_STORE_BUCKET"] == "urdu-pipeline-local"
    assert environment["REDIS_URL"] == "redis://redis:6379/0"
    assert environment["SERVICE_AUTH_TOKEN"]

    assert _depends_on_healthy(api, "postgres")
    assert _depends_on_healthy(api, "minio")
    assert _depends_on_healthy(api, "redis")
    assert not api.get("volumes"), "API image should not bind-mount the repo"


def test_processor_service_builds_from_processor_dockerfile_and_waits_for_api():
    config = _compose_config()
    processor = config["services"]["processor"]

    assert Path(processor["build"]["context"]) == REPO_ROOT
    assert processor["build"]["dockerfile"] == "Dockerfile.processor"
    assert processor["image"] == "urdu-pipeline-processor:local"

    command = _command_text(processor)
    assert "urdu-pipeline process" in command
    assert "--api-url http://api:8000" in command
    assert "--dry-run" not in command
    assert "processor-ready" in command
    assert "http.server" not in command

    healthcheck = _healthcheck_text(processor)
    assert "processor-ready" in healthcheck
    assert "ffprobe -version" in healthcheck

    environment = processor["environment"]
    assert environment["OUTPUT_ROOT"] == "/app/runs"
    assert environment["CACHE_ROOT"] == "/app/.cache_pipeline"
    assert environment["PROCESSOR_API_URL"] == "http://api:8000"
    assert environment["SERVICE_AUTH_TOKEN"]
    assert environment["OBJECT_STORE_ENDPOINT_URL"] == "http://minio:9000"
    assert environment["REDIS_URL"] == "redis://redis:6379/0"

    assert _depends_on_healthy(processor, "api")
    assert _depends_on_healthy(processor, "postgres")
    assert _depends_on_healthy(processor, "minio")
    assert _depends_on_healthy(processor, "redis")
    assert not processor.get("volumes"), "Processor image should not bind-mount the repo"


def test_dependency_services_expose_ports_healthchecks_and_persistent_volumes():
    config = _compose_config()
    services = config["services"]

    postgres = services["postgres"]
    assert postgres["image"] == "postgres:16-alpine"
    assert postgres["ports"][0]["target"] == 5432
    assert any(volume["target"] == "/var/lib/postgresql/data" for volume in postgres["volumes"])
    assert "pg_isready" in _healthcheck_text(postgres)

    minio = services["minio"]
    assert minio["image"] == "minio/minio:latest"
    assert {port["target"] for port in minio["ports"]} == {9000, 9001}
    assert any(volume["target"] == "/data" for volume in minio["volumes"])
    assert "/minio/health/ready" in _healthcheck_text(minio)

    redis = services["redis"]
    assert redis["image"] == "redis:7-alpine"
    assert redis["ports"][0]["target"] == 6379
    assert any(volume["target"] == "/data" for volume in redis["volumes"])
    assert "redis-cli ping" in _healthcheck_text(redis)


def test_optional_reverse_proxy_profile_proxies_to_api_service():
    config = _compose_config(profile="proxy")
    proxy = config["services"]["reverse-proxy"]

    assert proxy["image"] == "nginx:1.27-alpine"
    assert proxy["profiles"] == ["proxy"]
    assert _depends_on_healthy(proxy, "api")
    assert any(
        volume["target"] == "/etc/nginx/conf.d/default.conf"
        and volume.get("read_only") is True
        for volume in proxy["volumes"]
    )
    assert "http://127.0.0.1/health" in _healthcheck_text(proxy)

    nginx_conf = (REPO_ROOT / "deploy" / "nginx" / "default.conf").read_text(
        encoding="utf-8"
    )
    assert "proxy_pass http://api:8000" in nginx_conf
    assert "proxy_set_header Host $host" in nginx_conf
