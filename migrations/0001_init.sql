-- D1 (SQLite) schema for Grok2API on Cloudflare Workers

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
  token TEXT PRIMARY KEY,
  token_type TEXT NOT NULL CHECK (token_type IN ('sso', 'ssoSuper')),
  created_time INTEGER NOT NULL,
  remaining_queries INTEGER NOT NULL DEFAULT -1,
  heavy_remaining_queries INTEGER NOT NULL DEFAULT -1,
  status TEXT NOT NULL DEFAULT 'active',
  failed_count INTEGER NOT NULL DEFAULT 0,
  cooldown_until INTEGER,
  last_failure_time INTEGER,
  last_failure_reason TEXT,
  tags TEXT NOT NULL DEFAULT '[]',
  note TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tokens_type ON tokens(token_type);
CREATE INDEX IF NOT EXISTS idx_tokens_status ON tokens(status);
CREATE INDEX IF NOT EXISTS idx_tokens_cooldown_until ON tokens(cooldown_until);

CREATE TABLE IF NOT EXISTS api_keys (
  key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(is_active);

CREATE TABLE IF NOT EXISTS admin_sessions (
  token TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions(expires_at);

CREATE TABLE IF NOT EXISTS request_logs (
  id TEXT PRIMARY KEY,
  time TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  ip TEXT NOT NULL,
  model TEXT NOT NULL,
  duration REAL NOT NULL,
  status INTEGER NOT NULL,
  key_name TEXT NOT NULL,
  token_suffix TEXT NOT NULL,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens INTEGER NOT NULL DEFAULT 0,
  cached_tokens INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_request_logs_timestamp ON request_logs(timestamp);

CREATE TABLE IF NOT EXISTS token_refresh_progress (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  running INTEGER NOT NULL DEFAULT 0,
  current INTEGER NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0,
  success INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);

INSERT OR IGNORE INTO token_refresh_progress (id, running, current, total, success, failed, updated_at)
VALUES (1, 0, 0, 0, 0, 0, CAST(strftime('%s','now') AS INTEGER) * 1000);

-- Defaults (kept compatible with the existing admin UI fields)
INSERT OR IGNORE INTO settings (key, value, updated_at)
VALUES
  (
    'global',
    '{"base_url":"","log_level":"INFO","image_mode":"url","admin_password":"admin","admin_username":"admin","image_cache_max_size_mb":512,"video_cache_max_size_mb":1024}',
    CAST(strftime('%s','now') AS INTEGER) * 1000
  ),
  (
    'grok',
    '{"api_key":"","proxy_url":"","proxy_pool_url":"","proxy_pool_interval":300,"cache_proxy_url":"","cf_clearance":"","x_statsig_id":"","dynamic_statsig":true,"filtered_tags":"xaiartifact,xai:tool_usage_card","show_thinking":true,"temporary":false,"stream_first_response_timeout":30,"stream_chunk_timeout":120,"stream_total_timeout":600,"retry_status_codes":[401,429]}',
    CAST(strftime('%s','now') AS INTEGER) * 1000
  );

