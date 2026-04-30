"""End-to-end fake-provider pipeline test.

Builds an in-memory chunk manifest (no real audio I/O), then runs:
  transcriber -> reconciler -> translator -> article_generator
in a single fresh run directory.
"""

from __future__ import annotations

from urdu_pipeline.artifacts.exporter import export_run_zip
from urdu_pipeline.artifacts.store import ArtifactStore
from urdu_pipeline.schemas.chunks import ChunkManifestArtifact
from urdu_pipeline.stages.article_generator import run_article_stage
from urdu_pipeline.stages.transcriber import run_transcriber_stage
from urdu_pipeline.stages.transcript_reconciler import run_reconciler_stage
from urdu_pipeline.stages.translator import run_translator_stage


def _build_manifest_with_files(store: ArtifactStore, n: int = 3) -> ChunkManifestArtifact:
    chunks = []
    for i in range(1, n + 1):
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
                "overlap_after_ms": 0 if i == n else 60_000,
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
        "source_audio_duration_ms": 300_000 * n,
        "source_audio_format": "mp3",
        "chunk_length_seconds": 300,
        "overlap_seconds": 60,
        "chunks": chunks,
        "manifest": {
            "artifact_id": "cm_e2e",
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


def test_full_fake_pipeline_runs_end_to_end():
    store = ArtifactStore.for_new_run("e2e")
    manifest = _build_manifest_with_files(store, n=3)

    raw = run_transcriber_stage(chunk_manifest=manifest, store=store)
    assert raw.artifact_type == "raw_urdu_transcript"
    assert len(raw.chunks) == 3

    reconciled = run_reconciler_stage(raw=raw, store=store)
    assert reconciled.artifact_type == "reconciled_urdu_transcript"
    assert reconciled.full_text_urdu

    translation = run_translator_stage(reconciled=reconciled, store=store)
    assert translation.artifact_type == "english_translation"
    assert translation.full_text_english

    article = run_article_stage(translation=translation, store=store)
    assert article.artifact_type == "final_article"
    assert article.article.title
    assert article.article.body_markdown

    # Every stage produced both JSON and Markdown.
    artifacts = store.paths.artifacts
    for fn in (
        "raw_urdu_transcript.json",
        "raw_urdu_transcript.md",
        "reconciled_urdu_transcript.json",
        "reconciled_urdu_transcript.md",
        "english_translation.json",
        "english_translation.md",
        "final_article.json",
        "final_article.md",
    ):
        assert (artifacts / fn).exists(), f"{fn} missing"

    # Export ZIP gets created.
    target = export_run_zip(store.paths)
    assert target.exists()
