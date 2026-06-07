"""Compose fake-provider E2E smoke wiring."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_processor_compose_service_runs_real_loop_not_placeholder():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "urdu-pipeline process --api-url http://api:8000" in compose
    assert "--dry-run" not in compose
    assert "Job lifecycle loop not yet implemented" not in compose


def test_make_compose_test_runs_fake_provider_e2e_smoke():
    proc = subprocess.run(
        ["make", "--no-print-directory", "-n", "compose-test"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "compose-fake-provider-e2e" in proc.stdout
    assert "urdu_pipeline.tools.compose_fake_provider_e2e" in proc.stdout
