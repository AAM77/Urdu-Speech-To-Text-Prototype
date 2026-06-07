SHELL := /bin/bash
.SILENT:
.DEFAULT_GOAL := help

PYTHON ?= .venv/bin/python
PYTHONPATH ?= src
CLI = PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m urdu_pipeline.cli
UVICORN = PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m uvicorn
COMPOSE ?= docker compose
COMPOSE_ENV_FILE ?= .env.local.example
COMPOSE_CMD = $(COMPOSE) --env-file $(COMPOSE_ENV_FILE)

AUDIO ?=
RUN_DIR ?=
OUTPUT_ROOT ?= runs
RUNS_DIR ?= $(OUTPUT_ROOT)
CONFIRM_PAID_RUN ?= 0
CHUNK_LENGTH_SECONDS ?=
OVERLAP_SECONDS ?=
DATABASE_URL ?= postgresql://urdu_pipeline:urdu_pipeline_local_password@localhost:5432/urdu_pipeline
LOCAL_DATABASE_URL ?= $(DATABASE_URL)
API_HOST ?= 0.0.0.0
API_PORT ?= 8000
PROCESSOR_API_URL ?= http://localhost:8000
SERVICE_AUTH_TOKEN ?= local_processor_dev_token_change_me
LOCAL_USERNAME ?= local_user
LOCAL_PASSWORD ?= local_password_change_me
SERVICE_IDENTITY_NAME ?= processor
PROVIDER_NAME ?= fake
OBJECT_STORE_BUCKET ?= urdu-pipeline-local
OBJECT_STORE_REGION ?= local
LOCAL_OBJECT_STORE_ENDPOINT_URL ?= http://localhost:9000
OBJECT_STORE_ACCESS_KEY ?= urdu_pipeline
OBJECT_STORE_SECRET_KEY ?= urdu_pipeline_local_password

export OUTPUT_ROOT

PAID_FLAG = $(if $(filter 1 true TRUE yes YES y Y,$(CONFIRM_PAID_RUN)),--confirm-paid-run,)
CHUNK_LENGTH_FLAG = $(if $(strip $(CHUNK_LENGTH_SECONDS)),--chunk-length-seconds $(CHUNK_LENGTH_SECONDS),)
OVERLAP_FLAG = $(if $(strip $(OVERLAP_SECONDS)),--overlap-seconds $(OVERLAP_SECONDS),)

.PHONY: help latest-run chunk transcribe reconcile translate article to-transcribe to-reconcile to-translate to-article migrate-db api-dev processor-dev compose-up compose-down compose-test compose-setup compose-migrate compose-seed-user compose-seed-service-identity compose-seed-provider-config compose-seed-bucket

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
		'Local API stack' \
		'  make migrate-db            run PostgreSQL metadata migrations' \
		'  make api-dev               run the FastAPI app locally' \
		'  make processor-dev         validate/run the processor command locally' \
		'  make compose-up            build and start the local parity stack' \
		'  make compose-down          stop the local parity stack' \
		'  make compose-setup         run compose-up plus local setup commands' \
		'  make compose-test          validate and smoke-check the compose stack' \
		'  make compose-migrate       run DB migrations against compose PostgreSQL' \
		'  make compose-seed-user     create a local login user' \
		'  make compose-seed-bucket   ensure the local MinIO bucket exists' \
		'' \
		'Useful helper' \
		'  make latest-run'

latest-run:
	set -euo pipefail; $(RESOLVE_RUN_DIR)

compose-up:
	set -euo pipefail; $(COMPOSE_CMD) up --build -d --wait

compose-down:
	set -euo pipefail; $(COMPOSE_CMD) down

compose-test:
	set -euo pipefail; \
	$(COMPOSE_CMD) config >/dev/null; \
	$(COMPOSE_CMD) up --build -d --wait; \
	$(COMPOSE_CMD) ps; \
	$(COMPOSE_CMD) exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"; \
	$(COMPOSE_CMD) exec -T processor test -f /tmp/processor-ready

compose-setup:
	set -euo pipefail; \
	$(MAKE) compose-up; \
	$(MAKE) compose-migrate; \
	$(MAKE) compose-seed-bucket; \
	$(MAKE) compose-seed-user; \
	$(MAKE) compose-seed-service-identity; \
	$(MAKE) compose-seed-provider-config

compose-migrate:
	set -euo pipefail; $(CLI) migrate-db --database-url "$(LOCAL_DATABASE_URL)"

compose-seed-user:
	set -euo pipefail; $(CLI) admin-create-user --username "$(LOCAL_USERNAME)" --password "$(LOCAL_PASSWORD)" --database-url "$(LOCAL_DATABASE_URL)"

compose-seed-service-identity:
	set -euo pipefail; $(CLI) seed-service-identity --name "$(SERVICE_IDENTITY_NAME)" --database-url "$(LOCAL_DATABASE_URL)"

compose-seed-provider-config:
	set -euo pipefail; $(CLI) seed-provider-config --provider-name "$(PROVIDER_NAME)" --database-url "$(LOCAL_DATABASE_URL)"

compose-seed-bucket:
	set -euo pipefail; \
	OBJECT_STORE_ACCESS_KEY="$(OBJECT_STORE_ACCESS_KEY)" \
	OBJECT_STORE_SECRET_KEY="$(OBJECT_STORE_SECRET_KEY)" \
	AWS_ACCESS_KEY_ID="$(OBJECT_STORE_ACCESS_KEY)" \
	AWS_SECRET_ACCESS_KEY="$(OBJECT_STORE_SECRET_KEY)" \
	$(CLI) seed-bucket --bucket "$(OBJECT_STORE_BUCKET)" --endpoint-url "$(LOCAL_OBJECT_STORE_ENDPOINT_URL)" --region "$(OBJECT_STORE_REGION)"

migrate-db:
	set -euo pipefail; $(CLI) migrate-db --database-url "$(DATABASE_URL)"

api-dev:
	set -euo pipefail; $(UVICORN) urdu_pipeline.api.app:create_app --factory --host "$(API_HOST)" --port "$(API_PORT)"

processor-dev:
	set -euo pipefail; SERVICE_AUTH_TOKEN="$(SERVICE_AUTH_TOKEN)" $(CLI) process --api-url "$(PROCESSOR_API_URL)"

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
