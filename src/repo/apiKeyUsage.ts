import type { Env } from "../env";
import { dbAll, dbFirst } from "../db";
import { nowMs } from "../utils/time";

export interface ApiKeyUsageRow {
  key: string;
  day: string; // YYYY-MM-DD in configured local tz
  chat_used: number;
  heavy_used: number;
  image_used: number;
  video_used: number;
  updated_at: number;
}

export type ApiKeyUsageField = "chat_used" | "heavy_used" | "image_used" | "video_used";

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

export function localDayString(now = nowMs(), tzOffsetMinutes = 480): string {
  const offsetMs = tzOffsetMinutes * 60 * 1000;
  const local = new Date(now + offsetMs);
  const y = local.getUTCFullYear();
  const m = local.getUTCMonth() + 1;
  const d = local.getUTCDate();
  return `${y}-${pad2(m)}-${pad2(d)}`;
}

export async function listUsageForDay(db: Env["DB"], day: string): Promise<ApiKeyUsageRow[]> {
  return dbAll<ApiKeyUsageRow>(
    db,
    "SELECT key, day, chat_used, heavy_used, image_used, video_used, updated_at FROM api_key_usage_daily WHERE day = ?",
    [day],
  );
}

export async function getUsageForDay(
  db: Env["DB"],
  key: string,
  day: string,
): Promise<ApiKeyUsageRow | null> {
  return dbFirst<ApiKeyUsageRow>(
    db,
    "SELECT key, day, chat_used, heavy_used, image_used, video_used, updated_at FROM api_key_usage_daily WHERE key = ? AND day = ?",
    [key, day],
  );
}

export async function ensureUsageRow(db: Env["DB"], key: string, day: string, atMs: number): Promise<void> {
  await db
    .prepare("INSERT OR IGNORE INTO api_key_usage_daily(key, day, updated_at) VALUES(?,?,?)")
    .bind(key, day, atMs)
    .run();
}

export async function tryConsumeDailyUsage(args: {
  db: Env["DB"];
  key: string;
  day: string;
  field: ApiKeyUsageField;
  inc: number;
  limit: number; // -1 = unlimited
  atMs: number;
}): Promise<boolean> {
  const inc = Math.max(0, Math.floor(Number(args.inc) || 0));
  if (!inc) return true;

  await ensureUsageRow(args.db, args.key, args.day, args.atMs);

  const sql = `UPDATE api_key_usage_daily
    SET ${args.field} = ${args.field} + ?, updated_at = ?
    WHERE key = ? AND day = ? AND (? < 0 OR ${args.field} + ? <= ?)`;

  const res = await args.db
    .prepare(sql)
    .bind(inc, args.atMs, args.key, args.day, args.limit, inc, args.limit)
    .run();

  const changes = Number((res as any)?.meta?.changes ?? 0);
  return changes > 0;
}

export async function tryConsumeDailyUsageMulti(args: {
  db: Env["DB"];
  key: string;
  day: string;
  updates: Array<{ field: ApiKeyUsageField; inc: number; limit: number }>;
  atMs: number;
}): Promise<boolean> {
  const normalized = args.updates
    .map((u) => ({
      field: u.field,
      inc: Math.max(0, Math.floor(Number(u.inc) || 0)),
      limit: Math.floor(Number(u.limit) || -1),
    }))
    .filter((u) => u.inc > 0);

  if (!normalized.length) return true;
  await ensureUsageRow(args.db, args.key, args.day, args.atMs);

  const setParts: string[] = [];
  const whereParts: string[] = [];
  const params: unknown[] = [];

  // SET field = field + inc
  for (const u of normalized) {
    setParts.push(`${u.field} = ${u.field} + ?`);
    params.push(u.inc);
  }

  // updated_at last
  setParts.push("updated_at = ?");
  params.push(args.atMs);

  // WHERE base
  params.push(args.key, args.day);

  // WHERE quota conditions: (limit < 0 OR field + inc <= limit)
  for (const u of normalized) {
    whereParts.push("(? < 0 OR " + u.field + " + ? <= ?)");
    params.push(u.limit, u.inc, u.limit);
  }

  const sql = `UPDATE api_key_usage_daily
    SET ${setParts.join(", ")}
    WHERE key = ? AND day = ? AND ${whereParts.join(" AND ")}`;

  const res = await args.db.prepare(sql).bind(...params).run();
  const changes = Number((res as any)?.meta?.changes ?? 0);
  return changes > 0;
}
