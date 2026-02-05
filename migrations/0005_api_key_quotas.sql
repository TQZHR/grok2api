-- API key daily quotas + daily usage

-- Extend api_keys with per-day limits (-1 = unlimited)
ALTER TABLE api_keys ADD COLUMN chat_limit INTEGER NOT NULL DEFAULT -1;
ALTER TABLE api_keys ADD COLUMN heavy_limit INTEGER NOT NULL DEFAULT -1;
ALTER TABLE api_keys ADD COLUMN image_limit INTEGER NOT NULL DEFAULT -1;
ALTER TABLE api_keys ADD COLUMN video_limit INTEGER NOT NULL DEFAULT -1;

-- Per-key per-day usage counters
CREATE TABLE IF NOT EXISTS api_key_usage_daily (
  key TEXT NOT NULL,
  day TEXT NOT NULL,
  chat_used INTEGER NOT NULL DEFAULT 0,
  heavy_used INTEGER NOT NULL DEFAULT 0,
  image_used INTEGER NOT NULL DEFAULT 0,
  video_used INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (key, day)
);

CREATE INDEX IF NOT EXISTS idx_api_key_usage_day ON api_key_usage_daily(day);
