CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    name text NOT NULL,
    checksum_sha256 text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
