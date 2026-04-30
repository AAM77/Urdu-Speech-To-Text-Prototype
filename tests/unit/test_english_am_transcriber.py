"""American English chunk transcriber (fake provider)."""

from __future__ import annotations


from urdu_pipeline.artifacts.store import ArtifactStore
from urdu_pipeline.artifacts.validators import load_and_validate_artifact
from urdu_pipeline.cache.artifact_cache import ArtifactCache
from urdu_pipeline.providers.fake_provider import FakeAudioTranscriptionProvider
from urdu_pipeline.schemas.chunks import ChunkManifestArtifact
from urdu_pipeline.standalone.english_am_chunk_transcriber import run_english_am_transcriber


def _build_chunk_manifest_with_files(store: ArtifactStore, num_chunks: int = 2) -> ChunkManifestArtifact:
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


def test_fake_english_transcript_contains_markers(tmp_path):
    p = FakeAudioTranscriptionProvider()
    chunk = tmp_path / "chunk_0001.mp3"
    chunk.write_bytes(b"\x01")
    res = p.transcribe_chunk(
        chunk_path=chunk,
        prompt="x",
        model_id="fake-transcribe",
        language_hint="en",
    )
    assert "[chunk=1]" in res.text
    assert "[unclear]" in res.text


def test_english_am_writes_json_markdown_matches_layout():
    store = ArtifactStore.for_new_run("en-am-test")
    manifest = _build_chunk_manifest_with_files(store, num_chunks=2)
    artifact = run_english_am_transcriber(
        chunk_manifest=manifest,
        store=store,
        provider=FakeAudioTranscriptionProvider(),
    )

    json_path = store.paths.artifacts / "raw_am_english_transcript.json"
    md_path = store.paths.artifacts / "raw_am_english_transcript.md"
    assert json_path.exists()
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert md.startswith("# Raw American English Transcript")
    assert "## Chunk 1 (chunk_0001)" in md

    roundtrip = load_and_validate_artifact(json_path)
    assert roundtrip.artifact_type == "raw_am_english_transcript"
    assert artifact.artifact_type == "raw_am_english_transcript"
    assert artifact.manifest.stage_name == "english_chunk_transcriber"
    assert len(artifact.chunks) == 2
    assert "[unclear]" in artifact.chunks[0].text_english


def test_english_am_uses_distinct_cache_key_from_urdu_transcriber(tmp_path):
    store = ArtifactStore.for_new_run("en-cache-test")
    manifest = _build_chunk_manifest_with_files(store, num_chunks=1)
    fake = FakeAudioTranscriptionProvider()
    cache = ArtifactCache()
    run_english_am_transcriber(
        chunk_manifest=manifest,
        store=store,
        provider=fake,
        cache=cache,
    )
    first_calls = fake.call_count
    run_english_am_transcriber(
        chunk_manifest=manifest,
        store=store,
        provider=fake,
        cache=cache,
    )
    assert fake.call_count == first_calls

