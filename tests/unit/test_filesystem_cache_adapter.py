"""Filesystem cache adapter contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from urdu_pipeline.application.ports import CacheStore
from urdu_pipeline.application.ports.services import CacheScope
from urdu_pipeline.cache.artifact_cache import ArtifactCache
from urdu_pipeline.domain import UserId


def _scope(*, user_id: UserId | None = None, name: str = "translator") -> CacheScope:
    return CacheScope(user_id=user_id or UserId.new(), name=name)


def _artifact_cache_key(scope: CacheScope, key: str) -> str:
    return f"users/{scope.user_id}/{scope.name}/{key}"


def test_filesystem_cache_store_round_trip_matches_artifact_cache(tmp_path: Path):
    from urdu_pipeline.infrastructure.filesystem import FilesystemCacheStore

    cache = ArtifactCache(root=tmp_path / ".cache_pipeline")
    store = FilesystemCacheStore(cache=cache)
    scope = _scope(name="transcriber")

    assert isinstance(store, CacheStore)
    assert store.get(scope, "abc123") is None

    entry = store.put(scope, "abc123", {"text": "hello"})
    hit = store.get(scope, "abc123")
    artifact_hit = cache.lookup(_artifact_cache_key(scope, "abc123"))

    assert entry.scope == scope
    assert entry.key == "abc123"
    assert entry.payload == {"text": "hello"}
    assert hit is not None
    assert hit.payload == {"text": "hello"}
    assert artifact_hit.hit
    assert artifact_hit.payload == {"text": "hello"}


def test_filesystem_cache_store_corrupt_entry_is_treated_as_miss(tmp_path: Path):
    from urdu_pipeline.infrastructure.filesystem import FilesystemCacheStore

    cache = ArtifactCache(root=tmp_path / ".cache_pipeline")
    store = FilesystemCacheStore(cache=cache)
    scope = _scope(name="article")

    corrupt_path = cache._path_for(_artifact_cache_key(scope, "bad123"))
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_path.write_text("not-json", encoding="utf-8")

    assert store.get(scope, "bad123") is None
    assert not cache.lookup(_artifact_cache_key(scope, "bad123")).hit


def test_filesystem_cache_store_keeps_user_and_scope_entries_isolated(tmp_path: Path):
    from urdu_pipeline.infrastructure.filesystem import FilesystemCacheStore

    store = FilesystemCacheStore(root=tmp_path / ".cache_pipeline")
    first_user = UserId.new()
    second_user = UserId.new()
    first_scope = _scope(user_id=first_user, name="translator")
    second_scope = _scope(user_id=second_user, name="translator")
    third_scope = _scope(user_id=first_user, name="article")

    store.put(first_scope, "samekey", {"text": "first"})
    store.put(second_scope, "samekey", {"text": "second"})
    store.put(third_scope, "samekey", {"text": "third"})

    assert store.get(first_scope, "samekey").payload["text"] == "first"
    assert store.get(second_scope, "samekey").payload["text"] == "second"
    assert store.get(third_scope, "samekey").payload["text"] == "third"


def test_filesystem_cache_store_delete_removes_only_scoped_entry(tmp_path: Path):
    from urdu_pipeline.infrastructure.filesystem import FilesystemCacheStore

    store = FilesystemCacheStore(root=tmp_path / ".cache_pipeline")
    first_scope = _scope(name="translator")
    second_scope = _scope(name="article", user_id=first_scope.user_id)

    store.put(first_scope, "samekey", {"text": "first"})
    store.put(second_scope, "samekey", {"text": "second"})

    assert store.delete(first_scope, "samekey") is True
    assert store.delete(first_scope, "samekey") is False
    assert store.get(first_scope, "samekey") is None
    assert store.get(second_scope, "samekey").payload["text"] == "second"


@pytest.mark.parametrize(
    ("scope_name", "key"),
    [
        ("../translator", "abc123"),
        ("translator.v1", "abc123"),
        ("translator", "../abc123"),
        ("translator", "abc123.json"),
        ("translator", ""),
    ],
)
def test_filesystem_cache_store_rejects_unsafe_scope_or_key(
    tmp_path: Path,
    scope_name: str,
    key: str,
):
    from urdu_pipeline.infrastructure.filesystem import FilesystemCacheStore

    store = FilesystemCacheStore(root=tmp_path / ".cache_pipeline")

    with pytest.raises(ValueError):
        store.put(_scope(name=scope_name), key, {})
