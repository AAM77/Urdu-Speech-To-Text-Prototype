"""Cache key construction.

A cache key is a stable hash of:
- input hash (audio file hash or input text hash),
- stage name,
- model provider + model ID,
- prompt version,
- chunk length / overlap (where applicable),
- context mode,
- relevant model parameters.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def build_cache_key(
    *,
    input_hash: str,
    stage_name: str,
    model_provider: str,
    model_id: str,
    prompt_version: str | None,
    chunk_length_seconds: int | None = None,
    overlap_seconds: int | None = None,
    context_mode: str | None = None,
    model_parameters: dict[str, Any] | None = None,
) -> str:
    payload = {
        "input_hash": input_hash,
        "stage_name": stage_name,
        "model_provider": model_provider,
        "model_id": model_id,
        "prompt_version": prompt_version,
        "chunk_length_seconds": chunk_length_seconds,
        "overlap_seconds": overlap_seconds,
        "context_mode": context_mode,
        "model_parameters": model_parameters or {},
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
