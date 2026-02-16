-- Add missing settings sections for the multi-page admin UI (Workers/D1).
-- These keys are used by /api/v1/admin/config and are kept compatible with config.defaults.toml.

INSERT OR IGNORE INTO settings (key, value, updated_at)
VALUES
  (
    'token',
    '{"auto_refresh":true,"refresh_interval_hours":8,"fail_threshold":5,"save_delay_ms":500,"reload_interval_sec":30}',
    CAST(strftime('%s','now') AS INTEGER) * 1000
  ),
  (
    'cache',
    '{"enable_auto_clean":true,"limit_mb":1024,"keep_base64_cache":true}',
    CAST(strftime('%s','now') AS INTEGER) * 1000
  ),
  (
    'performance',
    '{"assets_max_concurrent":25,"media_max_concurrent":50,"usage_max_concurrent":25,"assets_delete_batch_size":10,"admin_assets_batch_size":10}',
    CAST(strftime('%s','now') AS INTEGER) * 1000
  ),
  (
    'register',
    '{"worker_domain":"","email_domain":"","admin_password":"","yescaptcha_key":"","solver_url":"http://127.0.0.1:5072","solver_browser_type":"camoufox","solver_threads":5,"register_threads":10,"default_count":100,"auto_start_solver":true,"solver_debug":false,"max_errors":0,"max_runtime_minutes":0}',
    CAST(strftime('%s','now') AS INTEGER) * 1000
  );

