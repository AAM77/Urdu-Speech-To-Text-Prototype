ALTER TABLE cleanup_tasks
    ADD COLUMN IF NOT EXISTS last_error text;
