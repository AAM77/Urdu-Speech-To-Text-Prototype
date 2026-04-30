"""Translator stage tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from urdu_pipeline.artifacts.store import ArtifactStore
from urdu_pipeline.artifacts.validators import (
    ArtifactValidationError,
    require_artifact_type,
)
from urdu_pipeline.providers.fake_provider import FakeTextGenerationProvider
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import (
    ReconciledSegment,
    ReconciledTranscriptArtifact,
)
from urdu_pipeline.stages.translator import run_translator_stage


def _reconciled(text: str = "بسم اللہ [غیر واضح] السلام علیکم") -> ReconciledTranscriptArtifact:
    seg = ReconciledSegment(
        segment_id="seg_0001",
        source_chunk_ids=["chunk_0001"],
        approx_start_ms=0,
        approx_end_ms=300_000,
        text_urdu=text,
    )
    manifest = ArtifactManifest(
        artifact_id="rec_test",
        stage_name="transcript_reconciler",
        artifact_type="reconciled_urdu_transcript",
        chunk_length_seconds=300,
        overlap_seconds=60,
    )
    return ReconciledTranscriptArtifact(
        source_audio_hash="h",
        raw_transcript_artifact_id="raw_test",
        segments=[seg],
        full_text_urdu=text,
        manifest=manifest,
    )


def test_fake_translator_writes_english():
    store = ArtifactStore.for_new_run("translate-test")
    artifact = run_translator_stage(reconciled=_reconciled(), store=store)
    assert (store.paths.artifacts / "english_translation.json").exists()
    assert (store.paths.artifacts / "english_translation.md").exists()
    assert artifact.artifact_type == "english_translation"
    assert "fake-translation" in artifact.full_text_english.lower()


def test_translator_preserves_uncertainty_marker():
    store = ArtifactStore.for_new_run("translate-unc")
    artifact = run_translator_stage(reconciled=_reconciled(), store=store)
    # Fake translation includes "[unclear]" as an English uncertainty marker.
    assert artifact.segments[0].preserved_uncertainty


def test_translator_rejects_wrong_artifact_via_validator(tmp_path):
    # Build a chunk_manifest payload and try to load it as a translation input.
    payload = {
        "artifact_type": "chunk_manifest",
        "schema_version": "1.0",
        "created_at": "2026-04-27T00:00:00Z",
        "source_audio_path": "x.mp3",
        "source_audio_hash": "h",
        "source_audio_duration_ms": 0,
        "source_audio_format": "mp3",
        "chunk_length_seconds": 300,
        "overlap_seconds": 60,
        "chunks": [],
        "manifest": {
            "artifact_id": "cm",
            "schema_version": "1.0",
            "stage_name": "chunker",
            "artifact_type": "chunk_manifest",
            "created_at": "2026-04-27T00:00:00Z",
            "source_input_hash": "h",
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
    p = tmp_path / "wrong.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        require_artifact_type(p, "reconciled_urdu_transcript")


def test_fake_provider_call_count_increments():
    p = FakeTextGenerationProvider()
    p.generate(prompt="translating Urdu to American English", input_text="hello", model_id="fake-text")
    assert p.call_count == 1
