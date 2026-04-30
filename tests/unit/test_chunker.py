"""Chunker tests (pure planner + filename safety)."""

from __future__ import annotations

import pytest

from urdu_pipeline.artifacts.store import sanitize_filename
from urdu_pipeline.stages.chunker import plan_chunks


def test_60_minute_audio_produces_15_chunks():
    chunks = plan_chunks(duration_seconds=3600, chunk_length_seconds=300, overlap_seconds=60)
    # step = 240s, total = 3600s. (3600 - 60) / 240 = 14.75 -> 15 chunks.
    assert len(chunks) == 15
    # Each new chunk starts every 240s.
    assert chunks[0].start_ms == 0
    assert chunks[1].start_ms == 240_000
    assert chunks[2].start_ms == 480_000
    assert chunks[3].start_ms == 720_000


def test_full_chunks_are_chunk_length_long_except_possibly_last():
    chunks = plan_chunks(duration_seconds=3600, chunk_length_seconds=300, overlap_seconds=60)
    for c in chunks[:-1]:
        assert c.duration_ms == 300_000
    # last chunk may be shorter
    assert chunks[-1].end_ms == 3_600_000
    assert chunks[-1].duration_ms <= 300_000


def test_short_audio_produces_one_short_chunk():
    chunks = plan_chunks(duration_seconds=120, chunk_length_seconds=300, overlap_seconds=60)
    assert len(chunks) == 1
    assert chunks[0].start_ms == 0
    assert chunks[0].end_ms == 120_000


def test_zero_duration_returns_empty():
    assert plan_chunks(0, 300, 60) == []


def test_invalid_overlap_raises():
    with pytest.raises(ValueError):
        plan_chunks(60, 300, 300)
    with pytest.raises(ValueError):
        plan_chunks(60, 300, -1)


def test_sanitize_filename_strips_path_separators_and_unsafe_chars():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    # Each unsafe character maps to an underscore individually (e.g. "; " -> "__").
    assert sanitize_filename("evil; rm -rf /") == "evil__rm_-rf"
    assert sanitize_filename(".hidden") == "hidden"
    assert sanitize_filename("normal_audio.mp3") == "normal_audio.mp3"
    # Empty / all-bad input falls back to "file".
    assert sanitize_filename("///") == "file"
