"""Reconciliation stage tests."""

from __future__ import annotations

from urdu_pipeline.artifacts.store import ArtifactStore
from urdu_pipeline.schemas.manifests import ArtifactManifest
from urdu_pipeline.schemas.transcripts import (
    RawTranscriptArtifact,
    RawTranscriptChunk,
)
from urdu_pipeline.stages.transcript_reconciler import (
    _stitch,
    run_reconciler_stage,
)


def _raw(chunks: list[str]) -> RawTranscriptArtifact:
    raw_chunks = [
        RawTranscriptChunk(
            chunk_id=f"chunk_{i+1:04d}",
            chunk_index=i + 1,
            start_ms=i * 240_000,
            end_ms=i * 240_000 + 300_000,
            text_urdu=text,
            uncertainty_markers=[],
            provider_metadata={},
        )
        for i, text in enumerate(chunks)
    ]
    manifest = ArtifactManifest(
        artifact_id="raw_test",
        stage_name="transcriber",
        artifact_type="raw_urdu_transcript",
        chunk_length_seconds=300,
        overlap_seconds=60,
    )
    return RawTranscriptArtifact(
        source_audio_hash="h",
        chunk_manifest_artifact_id="cm_test",
        chunks=raw_chunks,
        manifest=manifest,
    )


def test_stitch_drops_exact_duplicate_overlap():
    a = "salam alaikum kaise hain aap aaj"
    b = "kaise hain aap aaj kya hum baat kar sakte hain"
    stitched, trims = _stitch([a, b])
    assert trims == [4]  # the four-word overlap was removed
    assert "salam alaikum" in stitched
    assert "baat kar sakte" in stitched
    # The trimmed text appears exactly once.
    assert stitched.count("kaise hain aap aaj") == 1


def test_stitch_handles_no_overlap():
    a = "this is alpha"
    b = "completely different text"
    stitched, trims = _stitch([a, b])
    assert trims == [0]
    assert "alpha" in stitched and "different" in stitched


def test_reconciler_preserves_uncertainty_markers():
    raw = _raw(
        [
            "بسم اللہ سلام [غیر واضح] کیسے ہیں آپ آج",
            "کیسے ہیں آپ آج خوب",
        ]
    )
    store = ArtifactStore.for_new_run("recon-test")
    artifact = run_reconciler_stage(raw=raw, store=store)
    assert "[غیر واضح]" in artifact.full_text_urdu


def test_reconciler_keeps_chunk_references():
    raw = _raw(["alpha bravo charlie", "charlie delta echo"])
    store = ArtifactStore.for_new_run("recon-refs")
    artifact = run_reconciler_stage(raw=raw, store=store)
    chunk_ids: set[str] = set()
    for s in artifact.segments:
        chunk_ids.update(s.source_chunk_ids)
    assert chunk_ids == {"chunk_0001", "chunk_0002"}


def test_reconciler_writes_outputs():
    raw = _raw(["text one two", "two three four"])
    store = ArtifactStore.for_new_run("recon-out")
    run_reconciler_stage(raw=raw, store=store)
    assert (store.paths.artifacts / "reconciled_urdu_transcript.json").exists()
    assert (store.paths.artifacts / "reconciled_urdu_transcript.md").exists()
