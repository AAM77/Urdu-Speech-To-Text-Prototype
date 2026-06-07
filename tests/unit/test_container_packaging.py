"""Container packaging tests for the local API/processor stack."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_file(path: Path) -> str:
    assert path.is_file(), f"missing file: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _assert_installs_extra(dockerfile_text: str, *extras: str) -> None:
    normalized = dockerfile_text.replace("'", '"')
    for extra in extras:
        pattern = rf"pip\s+install\b[^\n]*\"\.\[[^\]]*\b{re.escape(extra)}\b[^\]]*\]\""
        assert re.search(pattern, normalized), f"missing .[{extra}] install"


def test_api_dockerfile_builds_api_runtime_and_starts_uvicorn_factory():
    text = _read_file(REPO_ROOT / "Dockerfile.api")

    assert "python:3.12-slim" in text
    _assert_installs_extra(text, "api")
    assert "EXPOSE 8000" in text
    assert "uvicorn" in text
    assert "urdu_pipeline.api.app:create_app" in text
    assert "--factory" in text
    assert "--host" in text
    assert "0.0.0.0" in text
    assert "--port" in text
    assert "8000" in text
    assert "http.server" not in text


def test_processor_dockerfile_installs_processor_cli_and_ffmpeg_tools():
    text = _read_file(REPO_ROOT / "Dockerfile.processor")

    assert "python:3.12-slim" in text
    _assert_installs_extra(text, "processor", "cli")
    assert re.search(r"apt-get\b[\s\S]*\binstall\b[\s\S]*\bffmpeg\b", text)
    assert "ffmpeg -version" in text
    assert "ffprobe -version" in text
    assert "urdu-pipeline process" in text


def test_dockerignore_excludes_local_state_and_secrets_from_build_context():
    text = _read_file(REPO_ROOT / ".dockerignore")
    ignored = {
        ".venv/",
        ".env",
        ".env.*",
        ".cache_pipeline/",
        ".pytest_cache/",
        "runs/",
        "__pycache__/",
    }

    lines = {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ignored.issubset(lines)
