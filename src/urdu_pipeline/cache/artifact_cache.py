"""Filesystem-backed artifact cache.

Stage code asks the cache for a key, and on a miss writes the result back.
Cache entries are JSON files keyed by the SHA-256 of the cache key payload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from urdu_pipeline.config.settings import Settings, get_settings


@dataclass(frozen=True)
class CacheLookupResult:
    hit: bool
    key: str
    path: Path
    payload: dict | None


class ArtifactCache:
    def __init__(self, root: Path | None = None, *, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self.root = (root or s.cache_root_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def lookup(self, key: str) -> CacheLookupResult:
        p = self._path_for(key)
        if p.exists():
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
                return CacheLookupResult(hit=True, key=key, path=p, payload=payload)
            except json.JSONDecodeError:
                # Corrupted entry — treat as miss.
                return CacheLookupResult(hit=False, key=key, path=p, payload=None)
        return CacheLookupResult(hit=False, key=key, path=p, payload=None)

    def store(self, key: str, payload: dict[str, Any]) -> Path:
        p = self._path_for(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return p

    def clear(self) -> int:
        n = 0
        for f in self.root.glob("*.json"):
            f.unlink()
            n += 1
        return n
