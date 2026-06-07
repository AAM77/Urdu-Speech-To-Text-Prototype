CREATE TABLE IF NOT EXISTS users (
    user_id text PRIMARY KEY,
    username text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_user_id_format CHECK (user_id ~ '^usr_[0-9a-f]{32}$'),
    CONSTRAINT users_status_check CHECK (
        status IN ('active', 'disabled', 'locked', 'deleted')
    ),
    UNIQUE (username)
);

CREATE TABLE IF NOT EXISTS service_identities (
    service_identity_id text PRIMARY KEY,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT service_identities_service_identity_id_format CHECK (
        service_identity_id ~ '^svc_[0-9a-f]{32}$'
    ),
    CONSTRAINT service_identities_status_check CHECK (
        status IN ('active', 'disabled', 'revoked')
    ),
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id text PRIMARY KEY,
    user_id text NOT NULL,
    session_hash text NOT NULL,
    scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    CONSTRAINT sessions_non_empty_id CHECK (length(session_id) > 0),
    CONSTRAINT sessions_expiry_after_created CHECK (expires_at > created_at),
    CONSTRAINT sessions_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE (session_hash)
);

CREATE TABLE IF NOT EXISTS api_tokens (
    api_token_id text PRIMARY KEY,
    principal_kind text NOT NULL,
    user_id text,
    service_identity_id text,
    token_hash text NOT NULL,
    scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    expires_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    CONSTRAINT api_tokens_non_empty_id CHECK (length(api_token_id) > 0),
    CONSTRAINT api_tokens_principal_kind_check CHECK (
        principal_kind IN ('user', 'service')
    ),
    CONSTRAINT api_tokens_principal_owner_check CHECK (
        (
            principal_kind = 'user'
            AND user_id IS NOT NULL
            AND service_identity_id IS NULL
        )
        OR (
            principal_kind = 'service'
            AND user_id IS NULL
            AND service_identity_id IS NOT NULL
        )
    ),
    CONSTRAINT api_tokens_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT api_tokens_service_identity_id_fk
        FOREIGN KEY (service_identity_id)
        REFERENCES service_identities(service_identity_id),
    UNIQUE (token_hash)
);

CREATE TABLE IF NOT EXISTS uploads (
    upload_id text PRIMARY KEY,
    user_id text NOT NULL,
    status text NOT NULL DEFAULT 'initialized',
    original_filename text,
    object_key text,
    content_type text,
    size_bytes bigint,
    checksum_sha256 text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    expires_at timestamptz,
    CONSTRAINT uploads_upload_id_format CHECK (upload_id ~ '^upl_[0-9a-f]{32}$'),
    CONSTRAINT uploads_status_check CHECK (
        status IN (
            'initialized',
            'uploading',
            'completed',
            'failed',
            'cancelled',
            'expired'
        )
    ),
    CONSTRAINT uploads_size_non_negative CHECK (
        size_bytes IS NULL OR size_bytes >= 0
    ),
    CONSTRAINT uploads_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id text PRIMARY KEY,
    user_id text NOT NULL,
    upload_id text,
    status text NOT NULL DEFAULT 'pending',
    provider_config_version_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    queued_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    cancelled_at timestamptz,
    CONSTRAINT runs_run_id_format CHECK (run_id ~ '^run_[0-9a-f]{32}$'),
    CONSTRAINT runs_status_check CHECK (
        status IN (
            'pending',
            'queued',
            'running',
            'succeeded',
            'failed',
            'cancelled'
        )
    ),
    CONSTRAINT runs_user_id_fk
        FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT runs_upload_id_fk
        FOREIGN KEY (upload_id) REFERENCES uploads(upload_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_service_identity_id
    ON api_tokens(service_identity_id);
CREATE INDEX IF NOT EXISTS idx_uploads_user_status ON uploads(user_id, status);
CREATE INDEX IF NOT EXISTS idx_uploads_created_at ON uploads(created_at);
CREATE INDEX IF NOT EXISTS idx_runs_user_status ON runs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_runs_upload_id ON runs(upload_id);
