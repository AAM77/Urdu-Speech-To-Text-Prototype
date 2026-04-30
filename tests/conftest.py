"""Shared pytest fixtures.

The fixtures here are deliberately small and fast: they isolate every test
into a temporary OUTPUT_ROOT and CACHE_ROOT, force fake-provider mode, and
strip any inherited OPENAI_API_KEY so tests cannot accidentally hit the real
network.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

# Make `src/` importable without installing the package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _isolate_environment(tmp_path: Path, monkeypatch):
    """Force fake mode + tmp output roots. Must run before settings load."""
    monkeypatch.setenv("PIPELINE_PROVIDER_MODE", "fake")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("CACHE_ROOT", str(tmp_path / ".cache_pipeline"))
    monkeypatch.setenv("DEFAULT_BUDGET_USD", "30")
    monkeypatch.setenv("HARD_CAP_USD", "60")
    monkeypatch.setenv("ACCEPTED_AUDIO_EXTENSIONS", "mp3,wav,m4a,flac,ogg,webm,mp4")
    monkeypatch.setenv("PROMPT_VERSION", "v1")
    monkeypatch.setenv(
        # Models default to fakes so tests don't depend on real model IDs.
        "TRANSCRIPTION_MODEL", "fake-transcribe",
    )
    monkeypatch.setenv("TRANSLATION_MODEL", "fake-text")
    monkeypatch.setenv("ARTICLE_MODEL", "fake-text")
    monkeypatch.setenv("RECONCILIATION_MODEL", "fake-text")

    from urdu_pipeline.config.settings import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def fresh_run_id() -> str:
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def make_chunk_manifest_payload():
    """Returns a callable that builds a minimal valid chunk_manifest dict."""
    def _make(num_chunks: int = 2) -> dict:
        chunks = []
        for i in range(1, num_chunks + 1):
            chunks.append(
                {
                    "chunk_id": f"chunk_{i:04d}",
                    "source_audio_hash": "deadbeef",
                    "chunk_index": i,
                    "start_ms": (i - 1) * 240_000,
                    "end_ms": (i - 1) * 240_000 + 300_000,
                    "duration_ms": 300_000,
                    "overlap_before_ms": 0 if i == 1 else 60_000,
                    "overlap_after_ms": 0 if i == num_chunks else 60_000,
                    "file_path": f"chunks/chunk_{i:04d}.mp3",
                    "file_hash": f"hash_{i:04d}",
                    "file_size_bytes": 100,
                    "audio_format": "mp3",
                }
            )
        manifest = {
            "artifact_id": "chunk_manifest_test123",
            "schema_version": "1.0",
            "stage_name": "chunker",
            "artifact_type": "chunk_manifest",
            "created_at": "2026-04-27T00:00:00Z",
            "source_input_hash": "deadbeef",
            "upstream_artifact_ids": [],
            "model_provider": None,
            "model_id": None,
            "prompt_id": None,
            "prompt_version": None,
            "chunk_length_seconds": 300,
            "overlap_seconds": 60,
            "context_mode": None,
            "estimated_cost_usd": None,
            "actual_usage": None,
            "cache_hit": False,
            "checksum": "",
            "warnings": [],
            "human_review_status": "unreviewed",
        }
        return {
            "artifact_type": "chunk_manifest",
            "schema_version": "1.0",
            "created_at": "2026-04-27T00:00:00Z",
            "source_audio_path": "input/sample.mp3",
            "source_audio_hash": "deadbeef",
            "source_audio_duration_ms": 300_000 * num_chunks,
            "source_audio_format": "mp3",
            "chunk_length_seconds": 300,
            "overlap_seconds": 60,
            "chunks": chunks,
            "manifest": manifest,
        }

    return _make
