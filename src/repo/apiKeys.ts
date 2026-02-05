import type { Env } from "../env";
import { dbAll, dbFirst, dbRun } from "../db";
import { generateApiKey } from "../utils/crypto";
import { nowMs } from "../utils/time";

export interface ApiKeyLimits {
  chat_limit: number; // per day, -1 = unlimited
  heavy_limit: number; // per day, -1 = unlimited
  image_limit: number; // per day, -1 = unlimited (count by generated images)
  video_limit: number; // per day, -1 = unlimited
}

export interface ApiKeyRow {
  key: string;
  name: string;
  created_at: number;
  is_active: number;
  chat_limit: number;
  heavy_limit: number;
  image_limit: number;
  video_limit: number;
}

export async function listApiKeys(db: Env["DB"]): Promise<ApiKeyRow[]> {
  return dbAll<ApiKeyRow>(
    db,
    "SELECT key, name, created_at, is_active, chat_limit, heavy_limit, image_limit, video_limit FROM api_keys ORDER BY created_at DESC",
  );
}

function normalizeLimit(v: unknown): number {
  if (v === null || v === undefined || v === "") return -1;
  const n = Number(v);
  if (!Number.isFinite(n)) return -1;
  return Math.max(-1, Math.floor(n));
}

export async function addApiKey(
  db: Env["DB"],
  name: string,
  opts?: { key?: string; limits?: Partial<ApiKeyLimits> },
): Promise<ApiKeyRow> {
  const key = String(opts?.key ?? "").trim() || generateApiKey();
  const created_at = Math.floor(nowMs() / 1000);
  const limits = opts?.limits ?? {};
  const chat_limit = normalizeLimit(limits.chat_limit);
  const heavy_limit = normalizeLimit(limits.heavy_limit);
  const image_limit = normalizeLimit(limits.image_limit);
  const video_limit = normalizeLimit(limits.video_limit);

  await dbRun(
    db,
    "INSERT INTO api_keys(key,name,created_at,is_active,chat_limit,heavy_limit,image_limit,video_limit) VALUES(?,?,?,1,?,?,?,?)",
    [key, name, created_at, chat_limit, heavy_limit, image_limit, video_limit],
  );
  return { key, name, created_at, is_active: 1, chat_limit, heavy_limit, image_limit, video_limit };
}

export async function batchAddApiKeys(
  db: Env["DB"],
  name_prefix: string,
  count: number,
): Promise<ApiKeyRow[]> {
  const created_at = Math.floor(nowMs() / 1000);
  const rows: ApiKeyRow[] = [];
  for (let i = 1; i <= count; i++) {
    const name = count > 1 ? `${name_prefix}-${i}` : name_prefix;
    const key = generateApiKey();
    rows.push({
      key,
      name,
      created_at,
      is_active: 1,
      chat_limit: -1,
      heavy_limit: -1,
      image_limit: -1,
      video_limit: -1,
    });
  }
  const batch = db.batch(
    rows.map((r) =>
      db
        .prepare(
          "INSERT INTO api_keys(key,name,created_at,is_active,chat_limit,heavy_limit,image_limit,video_limit) VALUES(?,?,?,1,?,?,?,?)",
        )
        .bind(r.key, r.name, r.created_at, r.chat_limit, r.heavy_limit, r.image_limit, r.video_limit),
    ),
  );
  await batch;
  return rows;
}

export async function deleteApiKey(db: Env["DB"], key: string): Promise<boolean> {
  const existing = await dbFirst<{ key: string }>(db, "SELECT key FROM api_keys WHERE key = ?", [key]);
  if (!existing) return false;
  await dbRun(db, "DELETE FROM api_keys WHERE key = ?", [key]);
  return true;
}

export async function batchDeleteApiKeys(db: Env["DB"], keys: string[]): Promise<number> {
  if (!keys.length) return 0;
  const placeholders = keys.map(() => "?").join(",");
  const before = await dbFirst<{ c: number }>(db, `SELECT COUNT(1) as c FROM api_keys WHERE key IN (${placeholders})`, keys);
  await dbRun(db, `DELETE FROM api_keys WHERE key IN (${placeholders})`, keys);
  return before?.c ?? 0;
}

export async function updateApiKeyStatus(db: Env["DB"], key: string, is_active: boolean): Promise<boolean> {
  const existing = await dbFirst<{ key: string }>(db, "SELECT key FROM api_keys WHERE key = ?", [key]);
  if (!existing) return false;
  await dbRun(db, "UPDATE api_keys SET is_active = ? WHERE key = ?", [is_active ? 1 : 0, key]);
  return true;
}

export async function batchUpdateApiKeyStatus(
  db: Env["DB"],
  keys: string[],
  is_active: boolean,
): Promise<number> {
  if (!keys.length) return 0;
  const placeholders = keys.map(() => "?").join(",");
  const before = await dbFirst<{ c: number }>(db, `SELECT COUNT(1) as c FROM api_keys WHERE key IN (${placeholders})`, keys);
  await dbRun(db, `UPDATE api_keys SET is_active = ? WHERE key IN (${placeholders})`, [is_active ? 1 : 0, ...keys]);
  return before?.c ?? 0;
}

export async function updateApiKeyName(db: Env["DB"], key: string, name: string): Promise<boolean> {
  const existing = await dbFirst<{ key: string }>(db, "SELECT key FROM api_keys WHERE key = ?", [key]);
  if (!existing) return false;
  await dbRun(db, "UPDATE api_keys SET name = ? WHERE key = ?", [name, key]);
  return true;
}

export async function updateApiKeyLimits(
  db: Env["DB"],
  key: string,
  limits: Partial<ApiKeyLimits>,
): Promise<boolean> {
  const existing = await dbFirst<{ key: string }>(db, "SELECT key FROM api_keys WHERE key = ?", [key]);
  if (!existing) return false;

  const parts: string[] = [];
  const params: unknown[] = [];
  if (limits.chat_limit !== undefined) {
    parts.push("chat_limit = ?");
    params.push(normalizeLimit(limits.chat_limit));
  }
  if (limits.heavy_limit !== undefined) {
    parts.push("heavy_limit = ?");
    params.push(normalizeLimit(limits.heavy_limit));
  }
  if (limits.image_limit !== undefined) {
    parts.push("image_limit = ?");
    params.push(normalizeLimit(limits.image_limit));
  }
  if (limits.video_limit !== undefined) {
    parts.push("video_limit = ?");
    params.push(normalizeLimit(limits.video_limit));
  }
  if (!parts.length) return true;
  params.push(key);

  await dbRun(db, `UPDATE api_keys SET ${parts.join(", ")} WHERE key = ?`, params);
  return true;
}

export async function validateApiKey(db: Env["DB"], key: string): Promise<{ key: string; name: string } | null> {
  const row = await dbFirst<{ key: string; name: string; is_active: number }>(
    db,
    "SELECT key, name, is_active FROM api_keys WHERE key = ?",
    [key],
  );
  if (!row) return null;
  if (!row.is_active) return null;
  return { key: row.key, name: row.name };
}

export async function getApiKeyLimits(db: Env["DB"], key: string): Promise<ApiKeyLimits | null> {
  const row = await dbFirst<ApiKeyLimits & { is_active: number }>(
    db,
    "SELECT is_active, chat_limit, heavy_limit, image_limit, video_limit FROM api_keys WHERE key = ?",
    [key],
  );
  if (!row) return null;
  if (!row.is_active) return null;
  return {
    chat_limit: Number(row.chat_limit ?? -1),
    heavy_limit: Number(row.heavy_limit ?? -1),
    image_limit: Number(row.image_limit ?? -1),
    video_limit: Number(row.video_limit ?? -1),
  };
}

