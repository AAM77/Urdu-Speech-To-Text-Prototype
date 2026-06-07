CREATE TABLE IF NOT EXISTS provider_config_versions (
    config_version_id text PRIMARY KEY,
    status text NOT NULL DEFAULT 'draft',
    provider_name text NOT NULL,
    description text,
    created_by_service_identity_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    disabled_at timestamptz,
    CONSTRAINT provider_config_versions_config_version_id_format CHECK (
        config_version_id ~ '^pcv_[0-9a-f]{32}$'
    ),
    CONSTRAINT provider_config_versions_status_check CHECK (
        status IN ('draft', 'active', 'disabled', 'retired')
    ),
    CONSTRAINT provider_config_versions_non_empty_provider CHECK (
        length(provider_name) > 0
    ),
    CONSTRAINT provider_config_versions_created_by_service_identity_id_fk
        FOREIGN KEY (created_by_service_identity_id)
        REFERENCES service_identities(service_identity_id)
);

CREATE TABLE IF NOT EXISTS provider_config_entries (
    config_version_id text NOT NULL,
    role text NOT NULL,
    model_id text NOT NULL,
    prompt_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT provider_config_entries_role_check CHECK (
        role IN ('transcription', 'translation', 'article', 'reconciliation')
    ),
    CONSTRAINT provider_config_entries_non_empty_model CHECK (length(model_id) > 0),
    CONSTRAINT provider_config_entries_config_version_id_fk
        FOREIGN KEY (config_version_id)
        REFERENCES provider_config_versions(config_version_id),
    UNIQUE (config_version_id, role)
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    prompt_version_id text PRIMARY KEY,
    prompt_id text NOT NULL,
    prompt_version text NOT NULL,
    stage_name text NOT NULL,
    body text NOT NULL,
    checksum_sha256 text NOT NULL,
    is_active boolean NOT NULL DEFAULT false,
    created_by_user_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT prompt_versions_non_empty_id CHECK (length(prompt_version_id) > 0),
    CONSTRAINT prompt_versions_non_empty_prompt CHECK (length(prompt_id) > 0),
    CONSTRAINT prompt_versions_non_empty_version CHECK (length(prompt_version) > 0),
    CONSTRAINT prompt_versions_stage_check CHECK (
        stage_name IN (
            'chunker',
            'transcriber',
            'transcript_reconciler',
            'translator',
            'article_generator',
            'english_chunk_transcriber'
        )
    ),
    CONSTRAINT prompt_versions_created_by_user_id_fk
        FOREIGN KEY (created_by_user_id) REFERENCES users(user_id),
    UNIQUE (prompt_id, prompt_version)
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id text PRIMARY KEY,
    user_id text NOT NULL,
    run_id text NOT NULL,
    stage text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    priority integer NOT NULL DEFAULT 0,
    routing jsonb NOT NULL DEFAULT '{}'::jsonb,
    lease_owner_service_identity_id text,
    lease_id text,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    queued_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT jobs_job_id_format CHECK (job_id ~ '^job_[0-9a-f]{32}$'),
    CONSTRAINT jobs_status_check CHECK (
        status IN (
            'queued',
            'claimed',
            'running',
            'succeeded',
            'failed',
            'cancelled',
            'dead_lettered'
        )
    ),
    CONSTRAINT jobs_stage_check CHECK (
        stage IN (
            'chunker',
            'transcriber',
            'transcript_reconciler',
            'translator',
            'article_generator',
            'english_chunk_transcriber'
        )
    ),
    CONSTRAINT jobs_priority_non_negative CHECK (priority >= 0),
    CONSTRAINT jobs_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT jobs_run_id_fk
        FOREIGN KEY (run_id) REFERENCES runs(run_id),
    CONSTRAINT jobs_lease_owner_service_identity_id_fk
        FOREIGN KEY (lease_owner_service_identity_id)
        REFERENCES service_identities(service_identity_id)
);

CREATE TABLE IF NOT EXISTS job_attempts (
    job_attempt_id text PRIMARY KEY,
    job_id text NOT NULL,
    user_id text NOT NULL,
    run_id text NOT NULL,
    attempt_number integer NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    worker_service_identity_id text,
    lease_id text,
    started_at timestamptz,
    completed_at timestamptz,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT job_attempts_non_empty_id CHECK (length(job_attempt_id) > 0),
    CONSTRAINT job_attempts_attempt_number_positive CHECK (attempt_number > 0),
    CONSTRAINT job_attempts_status_check CHECK (
        status IN (
            'pending',
            'running',
            'succeeded',
            'failed',
            'cancelled',
            'timed_out'
        )
    ),
    CONSTRAINT job_attempts_job_id_fk
        FOREIGN KEY (job_id) REFERENCES jobs(job_id),
    CONSTRAINT job_attempts_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT job_attempts_run_id_fk
        FOREIGN KEY (run_id) REFERENCES runs(run_id),
    CONSTRAINT job_attempts_worker_service_identity_id_fk
        FOREIGN KEY (worker_service_identity_id)
        REFERENCES service_identities(service_identity_id),
    UNIQUE (job_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id text PRIMARY KEY,
    user_id text NOT NULL,
    run_id text NOT NULL,
    job_id text NOT NULL,
    stage text NOT NULL,
    artifact_type text NOT NULL,
    object_key text NOT NULL,
    size_bytes bigint,
    checksum_sha256 text,
    manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT artifacts_artifact_id_format CHECK (artifact_id ~ '^art_[0-9a-f]{32}$'),
    CONSTRAINT artifacts_stage_check CHECK (
        stage IN (
            'chunker',
            'transcriber',
            'transcript_reconciler',
            'translator',
            'article_generator',
            'english_chunk_transcriber'
        )
    ),
    CONSTRAINT artifacts_type_check CHECK (
        artifact_type IN (
            'chunk_manifest',
            'raw_urdu_transcript',
            'reconciled_urdu_transcript',
            'english_translation',
            'final_article',
            'raw_am_english_transcript'
        )
    ),
    CONSTRAINT artifacts_size_non_negative CHECK (
        size_bytes IS NULL OR size_bytes >= 0
    ),
    CONSTRAINT artifacts_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT artifacts_run_id_fk
        FOREIGN KEY (run_id) REFERENCES runs(run_id),
    CONSTRAINT artifacts_job_id_fk
        FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE TABLE IF NOT EXISTS artifact_document_chunks (
    artifact_id text NOT NULL,
    chunk_index integer NOT NULL,
    user_id text NOT NULL,
    run_id text NOT NULL,
    text_content text NOT NULL,
    token_count integer,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT artifact_document_chunks_chunk_index_non_negative CHECK (
        chunk_index >= 0
    ),
    CONSTRAINT artifact_document_chunks_token_count_non_negative CHECK (
        token_count IS NULL OR token_count >= 0
    ),
    CONSTRAINT artifact_document_chunks_artifact_id_fk
        FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id),
    CONSTRAINT artifact_document_chunks_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT artifact_document_chunks_run_id_fk
        FOREIGN KEY (run_id) REFERENCES runs(run_id),
    UNIQUE (artifact_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS stage_events (
    stage_event_id text PRIMARY KEY,
    user_id text NOT NULL,
    run_id text NOT NULL,
    job_id text NOT NULL,
    stage text NOT NULL,
    event_type text NOT NULL,
    severity text NOT NULL DEFAULT 'info',
    message text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT stage_events_non_empty_id CHECK (length(stage_event_id) > 0),
    CONSTRAINT stage_events_non_empty_event_type CHECK (length(event_type) > 0),
    CONSTRAINT stage_events_severity_check CHECK (
        severity IN ('debug', 'info', 'warning', 'error')
    ),
    CONSTRAINT stage_events_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT stage_events_run_id_fk
        FOREIGN KEY (run_id) REFERENCES runs(run_id),
    CONSTRAINT stage_events_job_id_fk
        FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE TABLE IF NOT EXISTS provider_runs (
    provider_run_id text PRIMARY KEY,
    user_id text NOT NULL,
    run_id text NOT NULL,
    job_id text NOT NULL,
    provider_name text NOT NULL,
    model_id text NOT NULL,
    prompt_id text,
    prompt_version text,
    request_fingerprint text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    raw_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT provider_runs_provider_run_id_format CHECK (
        provider_run_id ~ '^prn_[0-9a-f]{32}$'
    ),
    CONSTRAINT provider_runs_non_empty_provider CHECK (length(provider_name) > 0),
    CONSTRAINT provider_runs_non_empty_model CHECK (length(model_id) > 0),
    CONSTRAINT provider_runs_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT provider_runs_run_id_fk
        FOREIGN KEY (run_id) REFERENCES runs(run_id),
    CONSTRAINT provider_runs_job_id_fk
        FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    usage_ledger_id text PRIMARY KEY,
    provider_run_id text NOT NULL,
    user_id text NOT NULL,
    run_id text NOT NULL,
    job_id text NOT NULL,
    provider_name text NOT NULL,
    model_id text NOT NULL,
    cost_usd numeric(12, 6) NOT NULL DEFAULT 0,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT usage_ledger_non_empty_id CHECK (length(usage_ledger_id) > 0),
    CONSTRAINT usage_ledger_cost_non_negative CHECK (cost_usd >= 0),
    CONSTRAINT usage_ledger_provider_run_id_fk
        FOREIGN KEY (provider_run_id) REFERENCES provider_runs(provider_run_id),
    CONSTRAINT usage_ledger_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT usage_ledger_run_id_fk
        FOREIGN KEY (run_id) REFERENCES runs(run_id),
    CONSTRAINT usage_ledger_job_id_fk
        FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE TABLE IF NOT EXISTS cache_entries (
    user_id text NOT NULL,
    scope_name text NOT NULL,
    cache_key text NOT NULL,
    payload jsonb NOT NULL,
    checksum_sha256 text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    CONSTRAINT cache_entries_non_empty_scope CHECK (length(scope_name) > 0),
    CONSTRAINT cache_entries_non_empty_key CHECK (length(cache_key) > 0),
    CONSTRAINT cache_entries_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE (user_id, scope_name, cache_key)
);

CREATE TABLE IF NOT EXISTS cleanup_tasks (
    cleanup_task_id text PRIMARY KEY,
    user_id text,
    run_id text,
    task_type text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    run_at timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT cleanup_tasks_cleanup_task_id_format CHECK (
        cleanup_task_id ~ '^cln_[0-9a-f]{32}$'
    ),
    CONSTRAINT cleanup_tasks_non_empty_type CHECK (length(task_type) > 0),
    CONSTRAINT cleanup_tasks_status_check CHECK (
        status IN (
            'pending',
            'running',
            'succeeded',
            'failed',
            'retrying',
            'cancelled'
        )
    ),
    CONSTRAINT cleanup_tasks_attempts_non_negative CHECK (attempts >= 0),
    CONSTRAINT cleanup_tasks_max_attempts_positive CHECK (max_attempts > 0),
    CONSTRAINT cleanup_tasks_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT cleanup_tasks_run_id_fk
        FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_config_versions_status
    ON provider_config_versions(status);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt_id_active
    ON prompt_versions(prompt_id, is_active);
CREATE INDEX IF NOT EXISTS idx_jobs_run_status ON jobs(run_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_job_attempts_job_status
    ON job_attempts(job_id, status);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_stage ON artifacts(run_id, stage);
CREATE INDEX IF NOT EXISTS idx_artifact_document_chunks_artifact
    ON artifact_document_chunks(artifact_id);
CREATE INDEX IF NOT EXISTS idx_stage_events_run_created_at
    ON stage_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_provider_runs_job_id ON provider_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_usage_ledger_run_id ON usage_ledger(run_id);
CREATE INDEX IF NOT EXISTS idx_cache_entries_scope
    ON cache_entries(user_id, scope_name);
CREATE INDEX IF NOT EXISTS idx_cleanup_tasks_status_run_at
    ON cleanup_tasks(status, run_at);
