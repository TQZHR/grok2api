import type { Env } from "../env";
import { dbAll, dbFirst, dbRun } from "../db";
import { nowMs } from "../utils/time";

export type TokenType = "sso" | "ssoSuper";

export interface TokenRow {
  token: string;
  token_type: TokenType;
  created_time: number;
  remaining_queries: number;
  heavy_remaining_queries: number;
  status: string;
  tags: string; // JSON string
  note: string;
  cooldown_until: number | null;
  last_failure_time: number | null;
  last_failure_reason: string | null;
  failed_count: number;
}

export interface TokenListFilters {
  token_type?: TokenType | "all";
  status?: string;
  nsfw?: string;
  search?: string;
  tag?: string;
}

export interface TokenListPageResult {
  total: number;
  items: TokenRow[];
}

const MAX_FAILURES = 3;

function parseTags(tagsJson: string): string[] {
  try {
    const v = JSON.parse(tagsJson) as unknown;
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function tokenRowToInfo(row: TokenRow): {
  token: string;
  token_type: TokenType;
  created_time: number;
  remaining_queries: number;
  heavy_remaining_queries: number;
  status: string;
  tags: string[];
  note: string;
  cooldown_until: number | null;
  last_failure_time: number | null;
  last_failure_reason: string;
  limit_reason: string;
  cooldown_remaining: number;
} {
  const now = nowMs();
  const cooldownRemainingMs =
    row.cooldown_until && row.cooldown_until > now ? row.cooldown_until - now : 0;
  const cooldown_remaining = cooldownRemainingMs ? Math.floor((cooldownRemainingMs + 999) / 1000) : 0;
  const limit_reason = cooldownRemainingMs
    ? "cooldown"
    : row.token_type === "ssoSuper"
      ? row.remaining_queries === 0 || row.heavy_remaining_queries === 0
        ? "exhausted"
        : ""
      : row.remaining_queries === 0
        ? "exhausted"
        : "";

  const status = (() => {
    if (row.status === "expired") return "失效";
    if (cooldownRemainingMs) return "冷却中";
    if (row.token_type === "ssoSuper") {
      if (row.remaining_queries === -1 && row.heavy_remaining_queries === -1) return "未使用";
      if (row.remaining_queries === 0 || row.heavy_remaining_queries === 0) return "额度耗尽";
      return "正常";
    }
    if (row.remaining_queries === -1) return "未使用";
    if (row.remaining_queries === 0) return "额度耗尽";
    return "正常";
  })();

  return {
    token: row.token,
    token_type: row.token_type,
    created_time: row.created_time,
    remaining_queries: row.remaining_queries,
    heavy_remaining_queries: row.heavy_remaining_queries,
    status,
    tags: parseTags(row.tags),
    note: row.note ?? "",
    cooldown_until: row.cooldown_until,
    last_failure_time: row.last_failure_time,
    last_failure_reason: row.last_failure_reason ?? "",
    limit_reason,
    cooldown_remaining,
  };
}

export async function listTokens(db: Env["DB"]): Promise<TokenRow[]> {
  return dbAll<TokenRow>(
    db,
    "SELECT token, token_type, created_time, remaining_queries, heavy_remaining_queries, status, tags, note, cooldown_until, last_failure_time, last_failure_reason, failed_count FROM tokens ORDER BY created_time DESC",
  );
}

function buildTokenWhere(filters?: TokenListFilters): { where: string; params: unknown[] } {
  const clauses: string[] = [];
  const params: unknown[] = [];

  const tokenType = filters?.token_type;
  if (tokenType === "sso" || tokenType === "ssoSuper") {
    clauses.push("token_type = ?");
    params.push(tokenType);
  }

  const search = String(filters?.search ?? "").trim();
  if (search) {
    clauses.push("token LIKE ?");
    params.push(`%${search}%`);
  }

  const tag = String(filters?.tag ?? "").trim();
  if (tag && tag !== "all") {
    clauses.push("tags LIKE ?");
    params.push(`%${tag.replace(/\"/g, "")}%`);
  }

  const nsfw = String(filters?.nsfw ?? "").trim().toLowerCase();
  if (nsfw) {
    if (["1", "true", "yes", "on", "enabled"].includes(nsfw)) {
      clauses.push("LOWER(note) LIKE '%nsfw%'");
    } else if (["0", "false", "no", "off", "disabled"].includes(nsfw)) {
      clauses.push("LOWER(note) NOT LIKE '%nsfw%'");
    }
  }

  const status = String(filters?.status ?? "").trim();
  if (status) {
    if (status === "invalid" || status === "失效") {
      clauses.push("status = 'expired'");
    } else if (status === "active" || status === "正常") {
      clauses.push("status != 'expired'");
      clauses.push("(cooldown_until IS NULL OR cooldown_until <= ?)");
      params.push(nowMs());
      clauses.push("(CASE WHEN token_type = 'ssoSuper' THEN (remaining_queries > 0 AND heavy_remaining_queries > 0) ELSE (remaining_queries > 0) END)");
    } else if (status === "cooling" || status === "冷却中") {
      clauses.push("status != 'expired'");
      clauses.push("cooldown_until IS NOT NULL AND cooldown_until > ?");
      params.push(nowMs());
    } else if (status === "exhausted" || status === "额度耗尽") {
      clauses.push("status != 'expired'");
      clauses.push("(cooldown_until IS NULL OR cooldown_until <= ?)");
      params.push(nowMs());
      clauses.push("(CASE WHEN token_type = 'ssoSuper' THEN (remaining_queries = 0 OR heavy_remaining_queries = 0) ELSE (remaining_queries = 0) END)");
    } else if (status === "unused" || status === "未使用") {
      clauses.push("status != 'expired'");
      clauses.push("(cooldown_until IS NULL OR cooldown_until <= ?)");
      params.push(nowMs());
      clauses.push("(CASE WHEN token_type = 'ssoSuper' THEN (remaining_queries = -1 AND heavy_remaining_queries = -1) ELSE (remaining_queries = -1) END)");
    }
  }

  return {
    where: clauses.length ? ` WHERE ${clauses.join(" AND ")}` : "",
    params,
  };
}

export async function listTokensPaged(
  db: Env["DB"],
  limit: number,
  offset: number,
  filters?: TokenListFilters,
): Promise<TokenListPageResult> {
  const { where, params } = buildTokenWhere(filters);
  const countRow = await dbFirst<{ c: number }>(db, `SELECT COUNT(1) as c FROM tokens${where}`, params);
  const total = countRow?.c ?? 0;

  const pageParams = [...params, limit, offset];
  const items = await dbAll<TokenRow>(
    db,
    `SELECT token, token_type, created_time, remaining_queries, heavy_remaining_queries, status, tags, note, cooldown_until, last_failure_time, last_failure_reason, failed_count FROM tokens${where} ORDER BY created_time DESC LIMIT ? OFFSET ?`,
    pageParams,
  );

  return { total, items };
}

export async function addTokens(db: Env["DB"], tokens: string[], token_type: TokenType): Promise<number> {
  const now = nowMs();
  const cleaned = tokens.map((t) => t.trim()).filter(Boolean);
  if (!cleaned.length) return 0;

  const stmts = cleaned.map((t) =>
    db
      .prepare(
        "INSERT OR REPLACE INTO tokens(token, token_type, created_time, remaining_queries, heavy_remaining_queries, status, failed_count, cooldown_until, last_failure_time, last_failure_reason, tags, note) VALUES(?,?,?,?,?,'active',0,NULL,NULL,NULL,'[]','')",
      )
      .bind(t, token_type, now, -1, -1),
  );
  await db.batch(stmts);
  return cleaned.length;
}

export async function deleteTokens(db: Env["DB"], tokens: string[], token_type: TokenType): Promise<number> {
  const cleaned = tokens.map((t) => t.trim()).filter(Boolean);
  if (!cleaned.length) return 0;
  const placeholders = cleaned.map(() => "?").join(",");
  const before = await dbFirst<{ c: number }>(
    db,
    `SELECT COUNT(1) as c FROM tokens WHERE token_type = ? AND token IN (${placeholders})`,
    [token_type, ...cleaned],
  );
  await dbRun(db, `DELETE FROM tokens WHERE token_type = ? AND token IN (${placeholders})`, [token_type, ...cleaned]);
  return before?.c ?? 0;
}

export async function updateTokenTags(db: Env["DB"], token: string, token_type: TokenType, tags: string[]): Promise<void> {
  const cleaned = tags.map((t) => t.trim()).filter(Boolean);
  await dbRun(db, "UPDATE tokens SET tags = ? WHERE token = ? AND token_type = ?", [
    JSON.stringify(cleaned),
    token,
    token_type,
  ]);
}

export async function updateTokenNote(db: Env["DB"], token: string, token_type: TokenType, note: string): Promise<void> {
  await dbRun(db, "UPDATE tokens SET note = ? WHERE token = ? AND token_type = ?", [note.trim(), token, token_type]);
}

export async function getAllTags(db: Env["DB"]): Promise<string[]> {
  const rows = await dbAll<{ tags: string }>(db, "SELECT tags FROM tokens");
  const set = new Set<string>();
  for (const r of rows) {
    for (const t of parseTags(r.tags)) set.add(t);
  }
  return [...set].sort();
}

export async function selectBestToken(db: Env["DB"], model: string): Promise<{ token: string; token_type: TokenType } | null> {
  const now = nowMs();
  const isHeavy = model === "grok-4-heavy";
  const field = isHeavy ? "heavy_remaining_queries" : "remaining_queries";

  const pick = async (token_type: TokenType): Promise<{ token: string; token_type: TokenType } | null> => {
    const row = await dbFirst<{ token: string }>(
      db,
      `SELECT token FROM tokens
       WHERE token_type = ?
         AND status != 'expired'
         AND failed_count < ?
         AND (cooldown_until IS NULL OR cooldown_until <= ?)
         AND ${field} != 0
       ORDER BY CASE WHEN ${field} = -1 THEN 0 ELSE 1 END, ${field} DESC, created_time ASC
       LIMIT 1`,
      [token_type, MAX_FAILURES, now],
    );
    return row ? { token: row.token, token_type } : null;
  };

  if (isHeavy) return pick("ssoSuper");

  return (await pick("sso")) ?? (await pick("ssoSuper"));
}

export async function recordTokenFailure(
  db: Env["DB"],
  token: string,
  status: number,
  message: string,
): Promise<void> {
  const now = nowMs();
  const reason = `${status}: ${message}`;
  await dbRun(
    db,
    "UPDATE tokens SET failed_count = failed_count + 1, last_failure_time = ?, last_failure_reason = ? WHERE token = ?",
    [now, reason, token],
  );

  const row = await dbFirst<{ failed_count: number }>(db, "SELECT failed_count FROM tokens WHERE token = ?", [token]);
  if (!row) return;
  if (status >= 400 && status < 500 && row.failed_count >= MAX_FAILURES) {
    await dbRun(db, "UPDATE tokens SET status = 'expired' WHERE token = ?", [token]);
  }
}

export async function applyCooldown(db: Env["DB"], token: string, status: number): Promise<void> {
  const now = nowMs();
  let until: number | null = null;
  if (status === 429) {
    const row = await dbFirst<{ remaining_queries: number }>(db, "SELECT remaining_queries FROM tokens WHERE token = ?", [token]);
    const remaining = row?.remaining_queries ?? -1;
    const seconds = remaining > 0 || remaining === -1 ? 3600 : 36000;
    until = now + seconds * 1000;
  } else {
    // Workers 不适合做“按请求次数”冷却，这里用短时间冷却近似替代。
    until = now + 30 * 1000;
  }
  await dbRun(db, "UPDATE tokens SET cooldown_until = ? WHERE token = ?", [until, token]);
}

export async function updateTokenLimits(
  db: Env["DB"],
  token: string,
  updates: { remaining_queries?: number; heavy_remaining_queries?: number },
): Promise<void> {
  const parts: string[] = [];
  const params: unknown[] = [];
  if (typeof updates.remaining_queries === "number") {
    parts.push("remaining_queries = ?");
    params.push(updates.remaining_queries);
  }
  if (typeof updates.heavy_remaining_queries === "number") {
    parts.push("heavy_remaining_queries = ?");
    params.push(updates.heavy_remaining_queries);
  }
  if (!parts.length) return;
  params.push(token);
  await dbRun(db, `UPDATE tokens SET ${parts.join(", ")} WHERE token = ?`, params);
}
