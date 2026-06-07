ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_hash text;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS description text;

ALTER TABLE uploads
    ADD COLUMN IF NOT EXISTS multipart_upload_id text;

ALTER TABLE api_tokens
    ADD COLUMN IF NOT EXISTS name text;

ALTER TABLE api_tokens
    ADD COLUMN IF NOT EXISTS description text;
