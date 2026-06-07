SHELL := /bin/bash
.SILENT:
.DEFAULT_GOAL := help

PYTHON ?= .venv/bin/python
PYTHONPATH ?= src
CLI = PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m urdu_pipeline.cli

AUDIO ?=
RUN_DIR ?=
OUTPUT_ROOT ?= runs
RUNS_DIR ?= $(OUTPUT_ROOT)
CONFIRM_PAID_RUN ?= 0
CHUNK_LENGTH_SECONDS ?=
OVERLAP_SECONDS ?=
DATABASE_URL ?= postgresql://urdu_pipeline:urdu_pipeline_local_password@localhost:5432/urdu_pipeline

export OUTPUT_ROOT

PAID_FLAG = $(if $(filter 1 true TRUE yes YES y Y,$(CONFIRM_PAID_RUN)),--confirm-paid-run,)
CHUNK_LENGTH_FLAG = $(if $(strip $(CHUNK_LENGTH_SECONDS)),--chunk-length-seconds $(CHUNK_LENGTH_SECONDS),)
OVERLAP_FLAG = $(if $(strip $(OVERLAP_SECONDS)),--overlap-seconds $(OVERLAP_SECONDS),)

.PHONY: help latest-run chunk transcribe reconcile translate article to-transcribe to-reconcile to-translate to-article migrate-db compose-up compose-test

REQUIRE_AUDIO = if [[ -z "$(AUDIO)" ]]; then echo "AUDIO is required. Example: make $@ AUDIO='inputs/example.mp3'"; exit 1; fi
RESOLVE_RUN_DIR = run_dir="$(RUN_DIR)"; if [[ -z "$$run_dir" ]]; then run_dir="$$(ls -td "$(RUNS_DIR)"/* 2>/dev/null | head -n1 || true)"; fi; if [[ -z "$$run_dir" ]]; then echo "No run directory found under $(RUNS_DIR). Pass RUN_DIR=... or create one with: make chunk AUDIO='inputs/example.mp3'"; exit 1; fi; echo "Using run directory: $$run_dir"

help:
	set -euo pipefail
	printf '%s\n' \
		'Available targets' \
		'' \
		'Single-stage targets' \
		'  make chunk AUDIO="inputs/file.mp3"' \
		'  make transcribe RUN_DIR="runs/<run-dir>"' \
		'  make reconcile RUN_DIR="runs/<run-dir>"' \
		'  make translate RUN_DIR="runs/<run-dir>"' \
		'  make article RUN_DIR="runs/<run-dir>"' \
		'' \
		'If RUN_DIR is omitted for single-stage targets, the latest run under OUTPUT_ROOT is used.' \
		'' \
		'Cumulative targets' \
		'  make to-transcribe AUDIO="inputs/file.mp3"' \
		'  make to-reconcile AUDIO="inputs/file.mp3"' \
		'  make to-translate AUDIO="inputs/file.mp3"' \
		'  make to-article AUDIO="inputs/file.mp3"' \
		'' \
		'Optional variables' \
		'  CONFIRM_PAID_RUN=1         required when PIPELINE_PROVIDER_MODE=real' \
		'  CHUNK_LENGTH_SECONDS=300   overrides chunk length for chunk/to-* targets' \
		'  OVERLAP_SECONDS=60         overrides overlap for chunk/to-* targets' \
		'  PYTHON=.venv/bin/python    overrides the interpreter used' \
		'' \
		'Local API stack placeholders' \
		'  make migrate-db            run PostgreSQL metadata migrations' \
		'  make compose-up            reserved for the future local API stack' \
		'  make compose-test          reserved for future local stack tests' \
		'' \
		'Useful helper' \
		'  make latest-run'

latest-run:
	set -euo pipefail; $(RESOLVE_RUN_DIR)

compose-up:
	set -euo pipefail; \
	echo "compose-up is not implemented yet."; \
	echo "The local stack skeleton can be validated with: docker compose --env-file .env.local.example config"; \
	exit 2

compose-test:
	set -euo pipefail; \
	echo "compose-test is not implemented yet."; \
	echo "The local stack skeleton can be validated with: docker compose --env-file .env.local.example config"; \
	exit 2

migrate-db:
	set -euo pipefail; $(CLI) migrate-db --database-url "$(DATABASE_URL)"

chunk:
	set -euo pipefail; $(REQUIRE_AUDIO); $(CLI) chunk --audio "$(AUDIO)" $(CHUNK_LENGTH_FLAG) $(OVERLAP_FLAG)

transcribe:
	set -euo pipefail; \
	$(RESOLVE_RUN_DIR); \
	chunk_manifest="$$run_dir/artifacts/chunk_manifest.json"; \
	if [[ ! -f "$$chunk_manifest" ]]; then echo "Missing chunk manifest: $$chunk_manifest"; echo "Create it first with: make chunk AUDIO='inputs/example.mp3'"; exit 1; fi; \
	$(CLI) transcribe --chunk-manifest "$$chunk_manifest" $(PAID_FLAG)

reconcile:
	set -euo pipefail; \
	$(RESOLVE_RUN_DIR); \
	transcript="$$run_dir/artifacts/raw_urdu_transcript.json"; \
	if [[ ! -f "$$transcript" ]]; then echo "Missing raw transcript: $$transcript"; echo "Create it first with: make transcribe RUN_DIR=\"$$run_dir\""; exit 1; fi; \
	$(CLI) reconcile --transcript "$$transcript"

translate:
	set -euo pipefail; \
	$(RESOLVE_RUN_DIR); \
	transcript="$$run_dir/artifacts/reconciled_urdu_transcript.json"; \
	if [[ ! -f "$$transcript" ]]; then echo "Missing reconciled transcript: $$transcript"; echo "Create it first with: make reconcile RUN_DIR=\"$$run_dir\""; exit 1; fi; \
	$(CLI) translate --transcript "$$transcript" $(PAID_FLAG)

article:
	set -euo pipefail; \
	$(RESOLVE_RUN_DIR); \
	translation="$$run_dir/artifacts/english_translation.json"; \
	if [[ ! -f "$$translation" ]]; then echo "Missing English translation: $$translation"; echo "Create it first with: make translate RUN_DIR=\"$$run_dir\""; exit 1; fi; \
	$(CLI) article --translation "$$translation" $(PAID_FLAG)

to-transcribe:
	set -euo pipefail; \
	$(REQUIRE_AUDIO); \
	$(CLI) chunk --audio "$(AUDIO)" $(CHUNK_LENGTH_FLAG) $(OVERLAP_FLAG); \
	$(RESOLVE_RUN_DIR); \
	$(CLI) transcribe --chunk-manifest "$$run_dir/artifacts/chunk_manifest.json" $(PAID_FLAG)

to-reconcile:
	set -euo pipefail; \
	$(REQUIRE_AUDIO); \
	$(CLI) chunk --audio "$(AUDIO)" $(CHUNK_LENGTH_FLAG) $(OVERLAP_FLAG); \
	$(RESOLVE_RUN_DIR); \
	$(CLI) transcribe --chunk-manifest "$$run_dir/artifacts/chunk_manifest.json" $(PAID_FLAG); \
	$(CLI) reconcile --transcript "$$run_dir/artifacts/raw_urdu_transcript.json"

to-translate:
	set -euo pipefail; \
	$(REQUIRE_AUDIO); \
	$(CLI) chunk --audio "$(AUDIO)" $(CHUNK_LENGTH_FLAG) $(OVERLAP_FLAG); \
	$(RESOLVE_RUN_DIR); \
	$(CLI) transcribe --chunk-manifest "$$run_dir/artifacts/chunk_manifest.json" $(PAID_FLAG); \
	$(CLI) reconcile --transcript "$$run_dir/artifacts/raw_urdu_transcript.json"; \
	$(CLI) translate --transcript "$$run_dir/artifacts/reconciled_urdu_transcript.json" $(PAID_FLAG)

to-article:
	set -euo pipefail; \
	$(REQUIRE_AUDIO); \
	$(CLI) chunk --audio "$(AUDIO)" $(CHUNK_LENGTH_FLAG) $(OVERLAP_FLAG); \
	$(RESOLVE_RUN_DIR); \
	$(CLI) transcribe --chunk-manifest "$$run_dir/artifacts/chunk_manifest.json" $(PAID_FLAG); \
	$(CLI) reconcile --transcript "$$run_dir/artifacts/raw_urdu_transcript.json"; \
	$(CLI) translate --transcript "$$run_dir/artifacts/reconciled_urdu_transcript.json" $(PAID_FLAG); \
	$(CLI) article --translation "$$run_dir/artifacts/english_translation.json" $(PAID_FLAG)
