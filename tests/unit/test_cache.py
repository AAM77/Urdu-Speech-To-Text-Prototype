"""Cache key + cache store tests."""

from __future__ import annotations

from urdu_pipeline.cache.artifact_cache import ArtifactCache
from urdu_pipeline.cache.cache_keys import build_cache_key


def _key(**overrides):
    base = dict(
        input_hash="abc",
        stage_name="transcriber",
        model_provider="fake",
        model_id="fake-transcribe",
        prompt_version="v1",
        chunk_length_seconds=300,
        overlap_seconds=60,
        context_mode="prev_chunk_tail",
        model_parameters={},
    )
    base.update(overrides)
    return build_cache_key(**base)


def test_same_inputs_produce_same_key():
    assert _key() == _key()


def test_changed_model_changes_key():
    assert _key(model_id="other") != _key()


def test_changed_prompt_version_changes_key():
    assert _key(prompt_version="v2") != _key()


def test_changed_input_hash_changes_key():
    assert _key(input_hash="zzz") != _key()


def test_cache_store_and_lookup_round_trip(tmp_path):
    cache = ArtifactCache(root=tmp_path / ".c")
    miss = cache.lookup("k1")
    assert not miss.hit
    cache.store("k1", {"text": "hello"})
    hit = cache.lookup("k1")
    assert hit.hit
    assert hit.payload == {"text": "hello"}


def test_corrupted_cache_entry_is_treated_as_miss(tmp_path):
    cache = ArtifactCache(root=tmp_path / ".c")
    p = cache._path_for("k2")
    p.write_text("not-json", encoding="utf-8")
    res = cache.lookup("k2")
    assert not res.hit


def test_cache_clear_removes_entries(tmp_path):
    cache = ArtifactCache(root=tmp_path / ".c")
    cache.store("a", {"x": 1})
    cache.store("b", {"x": 2})
    n = cache.clear()
    assert n == 2
    assert not cache.lookup("a").hit
