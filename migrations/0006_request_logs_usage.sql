-- Add token usage columns to request_logs

ALTER TABLE request_logs ADD COLUMN total_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE request_logs ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE request_logs ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE request_logs ADD COLUMN reasoning_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE request_logs ADD COLUMN cached_tokens INTEGER NOT NULL DEFAULT 0;