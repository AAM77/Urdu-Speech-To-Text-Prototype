"""Transcriber stage tests (fake provider only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from urdu_pipeline.artifacts.store import ArtifactStore
from urdu_pipeline.cache.artifact_cache import ArtifactCache
from urdu_pipeline.providers.fake_provider import FakeAudioTranscriptionProvider
from urdu_pipeline.schemas.chunks import ChunkManifestArtifact
from urdu_pipeline.stages.transcriber import run_transcriber_stage


def _build_chunk_manifest_with_files(store: ArtifactStore, num_chunks: int = 3) -> ChunkManifestArtifact:
    chunks = []
    for i in range(1, num_chunks + 1):
        chunk_path = store.paths.chunks / f"chunk_{i:04d}.mp3"
        chunk_path.write_bytes(b"\x00" * 100)
        chunks.append(
            {
                "chunk_id": f"chunk_{i:04d}",
                "source_audio_hash": "hash",
                "chunk_index": i,
                "start_ms": (i - 1) * 240_000,
                "end_ms": (i - 1) * 240_000 + 300_000,
                "duration_ms": 300_000,
                "overlap_before_ms": 0 if i == 1 else 60_000,
                "overlap_after_ms": 0 if i == num_chunks else 60_000,
                "file_path": str(chunk_path.relative_to(store.paths.root)),
                "file_hash": f"h_{i:04d}",
                "file_size_bytes": 100,
                "audio_format": "mp3",
            }
        )
    payload = {
        "artifact_type": "chunk_manifest",
        "schema_version": "1.0",
        "created_at": "2026-04-27T00:00:00Z",
        "source_audio_path": "input/sample.mp3",
        "source_audio_hash": "hash",
        "source_audio_duration_ms": 300_000 * num_chunks,
        "source_audio_format": "mp3",
        "chunk_length_seconds": 300,
        "overlap_seconds": 60,
        "chunks": chunks,
        "manifest": {
            "artifact_id": "cm_test",
            "schema_version": "1.0",
            "stage_name": "chunker",
            "artifact_type": "chunk_manifest",
            "created_at": "2026-04-27T00:00:00Z",
            "source_input_hash": "hash",
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
        },
    }
    return ChunkManifestArtifact.model_validate(payload)


def test_fake_provider_returns_urdu_text():
    p = FakeAudioTranscriptionProvider()
    res = p.transcribe_chunk(
        chunk_path=Path("chunk_0001.mp3"),
        prompt="x",
        model_id="fake-transcribe",
    )
    assert "[chunk=1]" in res.text
    # The fake transcript template includes Urdu script.
    assert "اللہ" in res.text


def test_transcriber_writes_json_and_markdown(tmp_path):
    store = ArtifactStore.for_new_run("test")
    manifest = _build_chunk_manifest_with_files(store, num_chunks=2)
    artifact = run_transcriber_stage(chunk_manifest=manifest, store=store)

    assert (store.paths.artifacts / "raw_urdu_transcript.json").exists()
    assert (store.paths.artifacts / "raw_urdu_transcript.md").exists()
    assert artifact.artifact_type == "raw_urdu_transcript"
    assert len(artifact.chunks) == 2
    # Each transcript chunk references its source chunk_id.
    assert {c.chunk_id for c in artifact.chunks} == {"chunk_0001", "chunk_0002"}


def test_transcriber_uses_cache_on_second_run(tmp_path):
    store = ArtifactStore.for_new_run("cache-test")
    manifest = _build_chunk_manifest_with_files(store, num_chunks=2)
    cache = ArtifactCache()

    provider1 = FakeAudioTranscriptionProvider()
    run_transcriber_stage(
        chunk_manifest=manifest, store=store, provider=provider1, cache=cache
    )
    assert provider1.call_count == 2

    provider2 = FakeAudioTranscriptionProvider()
    run_transcriber_stage(
        chunk_manifest=manifest, store=store, provider=provider2, cache=cache
    )
    # All entries cached -> provider2 should NOT be called.
    assert provider2.call_count == 0


def test_transcriber_does_not_call_real_openai(tmp_path, monkeypatch):
    store = ArtifactStore.for_new_run("safety-test")
    manifest = _build_chunk_manifest_with_files(store, num_chunks=1)

    # Make sure even importing the real provider would fail loudly.
    import sys

    if "openai" in sys.modules:
        monkeypatch.setattr(sys.modules["openai"], "OpenAI", _explode, raising=False)

    artifact = run_transcriber_stage(chunk_manifest=manifest, store=store)
    assert artifact.manifest.model_provider == "fake"


def _explode(*a, **kw):
    raise AssertionError("Real OpenAI client must not be instantiated in tests.")
