"""Integration tests for the Makefile orchestration targets.

These tests intentionally invoke `make` as a subprocess so they exercise the
real shell orchestration, target prerequisites, and CLI wiring rather than
mocking the wrapper behavior away.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def make_env(tmp_path: Path) -> dict[str, Path | dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_executable(
        bin_dir / "ffprobe",
        """#!/bin/bash
set -euo pipefail
printf '%s\\n' '{"format":{"duration":"620.0"}}'
""",
    )
    _write_executable(
        bin_dir / "ffmpeg",
        """#!/bin/bash
set -euo pipefail
target="${!#}"
mkdir -p "$(dirname "$target")"
printf 'fake chunk for %s\\n' "$target" > "$target"
""",
    )

    audio_path = tmp_path / "sample.mp3"
    audio_path.write_bytes(b"not-real-audio-but-good-enough-for-fake-ffmpeg")

    output_root = tmp_path / "runs"
    cache_root = tmp_path / ".cache_pipeline"

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "PYTHON": sys.executable,
            "PIPELINE_PROVIDER_MODE": "fake",
            "OPENAI_API_KEY": "",
            "OUTPUT_ROOT": str(output_root),
            "CACHE_ROOT": str(cache_root),
            "DEFAULT_BUDGET_USD": "30",
            "HARD_CAP_USD": "60",
            "PROMPT_VERSION": "v1",
            "TRANSCRIPTION_MODEL": "fake-transcribe",
            "TRANSLATION_MODEL": "fake-text",
            "ARTICLE_MODEL": "fake-text",
            "RECONCILIATION_MODEL": "fake-text",
            "LOG_LEVEL": "ERROR",
        }
    )

    return {
        "env": env,
        "audio": audio_path,
        "output_root": output_root,
        "cache_root": cache_root,
    }


def _run_make(
    args: list[str],
    *,
    env: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"make {' '.join(args)} failed with exit code {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )
    return proc


def _single_run_dir(output_root: Path) -> Path:
    run_dirs = [p for p in output_root.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1, f"expected 1 run dir, found {run_dirs}"
    return run_dirs[0]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_chunk_target_creates_manifest_and_respects_chunk_flags(make_env):
    env = make_env["env"]
    audio = make_env["audio"]
    output_root = make_env["output_root"]

    _run_make(
        [
            "chunk",
            f"AUDIO={audio}",
            "CHUNK_LENGTH_SECONDS=200",
            "OVERLAP_SECONDS=50",
        ],
        env=env,
    )

    run_dir = _single_run_dir(output_root)
    manifest_path = run_dir / "artifacts" / "chunk_manifest.json"
    summary_path = run_dir / "artifacts" / "chunk_summary.md"

    assert manifest_path.exists()
    assert summary_path.exists()

    manifest = _load_json(manifest_path)
    assert manifest["artifact_type"] == "chunk_manifest"
    assert manifest["chunk_length_seconds"] == 200
    assert manifest["overlap_seconds"] == 50
    assert len(manifest["chunks"]) == 4
    assert [c["start_ms"] for c in manifest["chunks"]] == [0, 150_000, 300_000, 450_000]
    assert manifest["chunks"][-1]["end_ms"] == 620_000
    assert (run_dir / manifest["source_audio_path"]).exists()
    for chunk in manifest["chunks"]:
        assert (run_dir / chunk["file_path"]).exists()


def test_single_stage_targets_run_end_to_end_on_an_existing_run(make_env):
    env = make_env["env"]
    audio = make_env["audio"]
    output_root = make_env["output_root"]

    _run_make(["chunk", f"AUDIO={audio}"], env=env)
    run_dir = _single_run_dir(output_root)

    _run_make(["transcribe", f"RUN_DIR={run_dir}"], env=env)
    raw_path = run_dir / "artifacts" / "raw_urdu_transcript.json"
    assert raw_path.exists()
    raw = _load_json(raw_path)
    assert raw["artifact_type"] == "raw_urdu_transcript"
    assert raw["chunks"]

    _run_make(["reconcile", f"RUN_DIR={run_dir}"], env=env)
    reconciled_path = run_dir / "artifacts" / "reconciled_urdu_transcript.json"
    assert reconciled_path.exists()
    reconciled = _load_json(reconciled_path)
    assert reconciled["artifact_type"] == "reconciled_urdu_transcript"
    assert reconciled["full_text_urdu"]

    _run_make(["translate", f"RUN_DIR={run_dir}"], env=env)
    translation_path = run_dir / "artifacts" / "english_translation.json"
    assert translation_path.exists()
    translation = _load_json(translation_path)
    assert translation["artifact_type"] == "english_translation"
    assert "[fake-translation]" in translation["full_text_english"].lower()

    _run_make(["article", f"RUN_DIR={run_dir}"], env=env)
    article_path = run_dir / "artifacts" / "final_article.json"
    assert article_path.exists()
    article = _load_json(article_path)
    assert article["artifact_type"] == "final_article"
    assert article["article"]["title"]


def test_transcribe_uses_latest_run_when_run_dir_is_omitted(make_env):
    env = make_env["env"]
    audio = make_env["audio"]
    output_root = make_env["output_root"]

    _run_make(["chunk", f"AUDIO={audio}"], env=env)
    _run_make(["transcribe"], env=env)

    run_dir = _single_run_dir(output_root)
    assert (run_dir / "artifacts" / "raw_urdu_transcript.json").exists()


@pytest.mark.parametrize(
    ("target", "present", "absent"),
    [
        (
            "to-transcribe",
            {"chunk_manifest.json", "raw_urdu_transcript.json"},
            {
                "reconciled_urdu_transcript.json",
                "english_translation.json",
                "final_article.json",
            },
        ),
        (
            "to-reconcile",
            {
                "chunk_manifest.json",
                "raw_urdu_transcript.json",
                "reconciled_urdu_transcript.json",
            },
            {"english_translation.json", "final_article.json"},
        ),
        (
            "to-translate",
            {
                "chunk_manifest.json",
                "raw_urdu_transcript.json",
                "reconciled_urdu_transcript.json",
                "english_translation.json",
            },
            {"final_article.json"},
        ),
        (
            "to-article",
            {
                "chunk_manifest.json",
                "raw_urdu_transcript.json",
                "reconciled_urdu_transcript.json",
                "english_translation.json",
                "final_article.json",
            },
            set(),
        ),
    ],
)
def test_cumulative_targets_stop_at_the_expected_stage(
    make_env,
    target: str,
    present: set[str],
    absent: set[str],
):
    env = make_env["env"]
    audio = make_env["audio"]
    output_root = make_env["output_root"]

    _run_make([target, f"AUDIO={audio}"], env=env)

    run_dir = _single_run_dir(output_root)
    artifacts_dir = run_dir / "artifacts"

    for name in present:
        assert (artifacts_dir / name).exists(), f"{name} should exist for {target}"
    for name in absent:
        assert not (artifacts_dir / name).exists(), f"{name} should not exist for {target}"


def test_chunk_target_requires_audio_argument(make_env):
    proc = _run_make(["chunk"], env=make_env["env"], check=False)

    assert proc.returncode != 0
    assert "AUDIO is required" in proc.stdout


def test_single_stage_target_reports_missing_prerequisite(make_env):
    output_root = make_env["output_root"]
    run_dir = output_root / "empty-run"
    run_dir.mkdir(parents=True)

    proc = _run_make(
        ["transcribe", f"RUN_DIR={run_dir}"],
        env=make_env["env"],
        check=False,
    )

    assert proc.returncode != 0
    assert "Missing chunk manifest" in proc.stdout


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("api-dev", ["uvicorn", "urdu_pipeline.api.app:create_app", "--factory"]),
        ("processor-dev", ["urdu_pipeline.cli", "process", "--api-url"]),
        ("compose-up", ["docker compose", "--env-file .env.local.example", "up --build -d"]),
        ("compose-down", ["docker compose", "--env-file .env.local.example", "down"]),
        ("compose-test", ["docker compose", "config", "up --build -d", "ps"]),
    ],
)
def test_local_stack_runtime_targets_are_wired_for_dry_run(
    make_env,
    target: str,
    expected: list[str],
):
    proc = _run_make(["-n", target], env=make_env["env"])

    assert "not implemented yet" not in proc.stdout
    for snippet in expected:
        assert snippet in proc.stdout


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("compose-migrate", ["urdu_pipeline.cli", "migrate-db"]),
        ("compose-seed-user", ["urdu_pipeline.cli", "admin-create-user", "--username", "--password"]),
        ("compose-seed-service-identity", ["urdu_pipeline.cli", "seed-service-identity", "--name"]),
        ("compose-seed-provider-config", ["urdu_pipeline.cli", "seed-provider-config", "--provider-name"]),
        ("compose-seed-bucket", ["urdu_pipeline.cli", "seed-bucket", "--bucket", "--endpoint-url"]),
    ],
)
def test_local_stack_setup_targets_are_wired_for_dry_run(
    make_env,
    target: str,
    expected: list[str],
):
    proc = _run_make(["-n", target], env=make_env["env"])

    assert "urdu_pipeline.cli" in proc.stdout
    for snippet in expected:
        assert snippet in proc.stdout


def test_compose_setup_runs_all_setup_steps_in_order(make_env):
    proc = _run_make(["-n", "compose-setup"], env=make_env["env"])

    stdout = proc.stdout
    expected_order = [
        "compose-up",
        "compose-migrate",
        "compose-seed-bucket",
        "compose-seed-user",
        "compose-seed-service-identity",
        "compose-seed-provider-config",
    ]
    positions = [stdout.index(step) for step in expected_order]
    assert positions == sorted(positions)


def test_confirm_paid_run_flag_is_forwarded_to_paid_targets(make_env):
    proc = _run_make(
        [
            "-n",
            "to-article",
            f"AUDIO={make_env['audio']}",
            "CONFIRM_PAID_RUN=1",
        ],
        env=make_env["env"],
    )

    assert proc.stdout.count("--confirm-paid-run") == 3
    assert "reconcile --transcript" in proc.stdout
