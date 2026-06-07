"""Checks for local API workflow documentation."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "local_api_workflow.md"


def test_local_api_workflow_doc_exists_and_covers_required_topics():
    assert DOC_PATH.is_file(), "missing docs/local_api_workflow.md"

    text = DOC_PATH.read_text(encoding="utf-8")
    lower = text.lower()

    required_snippets = [
        "fake-provider mode",
        "make compose-setup",
        "make compose-test",
        "post /auth/login",
        "csrf_token",
        "post /tokens",
        "post /uploads/direct",
        "post /uploads/init",
        "post /runs",
        "get /runs/{run_id}",
        "get /runs/{run_id}/events",
        "get /runs/{run_id}/artifacts",
        "get /artifacts/{artifact_id}",
        "get /artifacts/{artifact_id}/download",
        "post /runs/{run_id}/cancel",
        "retry",
        "cleanup",
        "make compose-down",
        "object keys",
        "service_auth_token",
    ]

    for snippet in required_snippets:
        assert snippet in lower


def test_readme_links_to_local_api_workflow_doc():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/local_api_workflow.md" in readme
