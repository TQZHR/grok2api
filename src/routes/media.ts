import { Hono } from "hono";
import type { Env } from "../env";
import { getSettings, normalizeCfCookie } from "../settings";
import { applyCooldown, recordTokenFailure, selectBestToken } from "../repo/tokens";
import { getDynamicHeaders } from "../grok/headers";
import { deleteCacheRow, touchCacheRow, upsertCacheRow, type CacheType } from "../repo/cache";
import { nowMs } from "../utils/time";
import { nextLocalMidnightExpirationSeconds } from "../kv/cleanup";

export const mediaRoutes = new Hono<{ Bindings: Env }>();

function guessCacheSeconds(path: string): number {
  const lower = path.toLowerCase();
  if (lower.endsWith(".mp4") || lower.endsWith(".webm") || lower.endsWith(".mov")) return 60 * 60 * 24;
  return 60 * 60 * 24;
}

function detectTypeByPath(path: string): CacheType {
  const lower = path.toLowerCase();
  if (lower.endsWith(".mp4") || lower.endsWith(".webm") || lower.endsWith(".mov") || lower.endsWith(".avi"))
    return "video";
  return "image";
}

function r2Key(type: CacheType, imgPath: string): string {
  return `${type}/${imgPath}`;
}

function parseIntSafe(v: string | undefined, fallback: number): number {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.floor(n);
}

function base64UrlDecode(input: string): string {
  const s = input.replace(/-/g, "+").replace(/_/g, "/");
  const pad = s.length % 4 === 0 ? "" : "=".repeat(4 - (s.length % 4));
  const binary = atob(s + pad);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

function isAllowedUpstreamHost(hostname: string): boolean {
  const h = hostname.toLowerCase();
  return h === "assets.grok.com" || h === "grok.com" || h.endsWith(".grok.com") || h.endsWith(".x.ai");
}

function isUuid(s: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
}

// Legacy decoder for paths like:
// users-<uuid>-generated-<uuid>-image.jpg  -> /users/<uuid>/generated/<uuid>/image.jpg
function decodeLegacyHyphenPath(imgPath: string): string | null {
  const marker = "-generated-";
  const idx = imgPath.indexOf(marker);
  if (idx <= 0) return null;

  const left = imgPath.slice(0, idx);
  const right = imgPath.slice(idx + marker.length);

  if (!left.startsWith("users-")) return null;
  const userId = left.slice("users-".length);
  if (!isUuid(userId)) return null;

  // right: <uuid>-<filename>
  if (right.length < 36 + 1) return null;
  const genId = right.slice(0, 36);
  if (!isUuid(genId)) return null;
  if (right[36] !== "-") return null;
  const filename = right.slice(37);
  if (!filename) return null;

  return `/users/${userId}/generated/${genId}/${filename}`;
}

function responseFromBytes(args: {
  bytes: ArrayBuffer;
  contentType: string;
  cacheSeconds: number;
  rangeHeader: string | undefined;
}): Response {
  const headers = new Headers();
  headers.set("Accept-Ranges", "bytes");
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Cache-Control", `public, max-age=${args.cacheSeconds}`);
  headers.set("Content-Type", args.contentType || "application/octet-stream");

  const size = args.bytes.byteLength;
  const rangeHeader = args.rangeHeader;
  if (rangeHeader) {
    const m = rangeHeader.match(/^bytes=(\d*)-(\d*)$/);
    if (m) {
      const startStr = m[1] ?? "";
      const endStr = m[2] ?? "";

      // suffix-byte-range-spec: bytes=-500
      if (!startStr && endStr) {
        const suffix = Number(endStr);
        if (!Number.isFinite(suffix) || suffix <= 0) return new Response(null, { status: 416 });
        const length = Math.min(size, suffix);
        const start = Math.max(0, size - length);
        const end = size - 1;
        const sliced = args.bytes.slice(start, end + 1);
        headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
        headers.set("Content-Length", String(sliced.byteLength));
        return new Response(sliced, { status: 206, headers });
      }

      let start = startStr ? Number(startStr) : 0;
      let end = endStr ? Number(endStr) : size - 1;
      if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end < 0 || start > end) {
        return new Response(null, { status: 416 });
      }
      if (start >= size) return new Response(null, { status: 416 });
      end = Math.min(end, size - 1);
      const sliced = args.bytes.slice(start, end + 1);
      headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
      headers.set("Content-Length", String(sliced.byteLength));
      return new Response(sliced, { status: 206, headers });
    }
  }

  headers.set("Content-Length", String(size));
  return new Response(args.bytes, { status: 200, headers });
}

function toUpstreamHeaders(args: { pathname: string; cookie: string; settings: Awaited<ReturnType<typeof getSettings>>["grok"] }): Record<string, string> {
  const headers = getDynamicHeaders(args.settings, args.pathname);
  headers.Cookie = args.cookie;
  delete headers["Content-Type"];
  headers.Accept =
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8";
  headers["Sec-Fetch-Dest"] = "document";
  headers["Sec-Fetch-Mode"] = "navigate";
  headers["Sec-Fetch-Site"] = "same-site";
  headers["Sec-Fetch-User"] = "?1";
  headers["Upgrade-Insecure-Requests"] = "1";
  headers.Referer = "https://grok.com/";
  return headers;
}

mediaRoutes.get("/images/:imgPath{.+}", async (c) => {
  const imgPath = c.req.param("imgPath");

  let upstreamPath: string | null = null;
  let upstreamUrl: URL | null = null;

  // New encoding: p_<base64url(pathname)>
  if (imgPath.startsWith("p_")) {
    try {
      upstreamPath = base64UrlDecode(imgPath.slice(2));
    } catch {
      upstreamPath = null;
    }
  }

  // New encoding: u_<base64url(full_url)>
  if (imgPath.startsWith("u_")) {
    try {
      const decodedUrl = base64UrlDecode(imgPath.slice(2));
      const u = new URL(decodedUrl);
      if (isAllowedUpstreamHost(u.hostname)) upstreamUrl = u;
    } catch {
      upstreamUrl = null;
    }
  }

  if (upstreamUrl) upstreamPath = upstreamUrl.pathname;

  // Legacy encoding (best-effort): users-<uuid>-generated-<uuid>-image.jpg
  if (!upstreamPath) upstreamPath = decodeLegacyHyphenPath(imgPath);

  // Very old encoding (lossy): replace '-' with '/' (breaks UUIDs)
  if (!upstreamPath) upstreamPath = `/${imgPath.replaceAll("-", "/")}`;

  // If upstreamPath accidentally contains a full URL, extract pathname.
  if (upstreamPath.startsWith("http://") || upstreamPath.startsWith("https://")) {
    try {
      upstreamPath = new URL(upstreamPath).pathname;
    } catch {
      // keep as-is
    }
  }

  if (!upstreamPath.startsWith("/")) upstreamPath = `/${upstreamPath}`;
  upstreamPath = upstreamPath.replace(/\/{2,}/g, "/");

  const originalPath = upstreamUrl?.pathname ?? upstreamPath;
  const url = upstreamUrl ?? new URL(`https://assets.grok.com${originalPath}`);
  const type = detectTypeByPath(originalPath);
  const key = r2Key(type, imgPath);
  const cacheSeconds = guessCacheSeconds(originalPath);

  const rangeHeader = c.req.header("Range");
  const cached = await c.env.KV_CACHE.getWithMetadata<{ contentType?: string; size?: number }>(key, {
    type: "arrayBuffer",
  });
  if (cached?.value) {
    c.executionCtx.waitUntil(touchCacheRow(c.env.DB, key, nowMs()));
    const contentType = (cached.metadata?.contentType as string | undefined) ?? "application/octet-stream";
    return responseFromBytes({ bytes: cached.value, contentType, cacheSeconds, rangeHeader });
  }

  // stale metadata cleanup (best-effort)
  c.executionCtx.waitUntil(deleteCacheRow(c.env.DB, key));

  const settingsBundle = await getSettings(c.env);
  const chosen = await selectBestToken(c.env.DB, "grok-4-fast");
  if (!chosen) return c.text("No available token", 503);

  const cf = normalizeCfCookie(settingsBundle.grok.cf_clearance ?? "");
  const cookie = cf ? `sso-rw=${chosen.token};sso=${chosen.token};${cf}` : `sso-rw=${chosen.token};sso=${chosen.token}`;

  const baseHeaders = toUpstreamHeaders({ pathname: originalPath, cookie, settings: settingsBundle.grok });

  // Range requests: KV can't stream partial content efficiently; proxy from upstream.
  // (If the object is cached and within KV limits, we do support Range by slicing bytes above.)
  const upstream = await fetch(url.toString(), { headers: rangeHeader ? { ...baseHeaders, Range: rangeHeader } : baseHeaders });
  if (!upstream.ok || !upstream.body) {
    const txt = await upstream.text().catch(() => "");
    await recordTokenFailure(c.env.DB, chosen.token, upstream.status, txt.slice(0, 200));
    await applyCooldown(c.env.DB, chosen.token, upstream.status);
    return new Response(`Upstream ${upstream.status}`, { status: upstream.status });
  }

  const contentType = upstream.headers.get("content-type") ?? "";
  const contentLengthHeader = upstream.headers.get("content-length") ?? "";
  const contentLength = contentLengthHeader ? Number(contentLengthHeader) : NaN;
  const maxBytes = Math.min(25 * 1024 * 1024, Math.max(1, parseIntSafe(c.env.KV_CACHE_MAX_BYTES, 25 * 1024 * 1024)));
  const shouldTryCache =
    !rangeHeader &&
    (!Number.isFinite(contentLength) || (contentLength > 0 && contentLength <= maxBytes));

  if (shouldTryCache) {
    const [toKvRaw, toClient] = upstream.body.tee();
    const tzOffset = parseIntSafe(c.env.CACHE_RESET_TZ_OFFSET_MINUTES, 480);
    const expiresAt = nextLocalMidnightExpirationSeconds(nowMs(), tzOffset);

    c.executionCtx.waitUntil(
      (async () => {
        try {
          let byteCount = 0;
          const limiter = new TransformStream<Uint8Array, Uint8Array>({
            transform(chunk, controller) {
              byteCount += chunk.byteLength;
              if (byteCount > maxBytes) throw new Error("KV value too large");
              controller.enqueue(chunk);
            },
          });
          const toKv = toKvRaw.pipeThrough(limiter);

          await c.env.KV_CACHE.put(key, toKv, {
            expiration: expiresAt,
            metadata: { contentType, size: Number.isFinite(contentLength) ? contentLength : byteCount, type },
          });
          const now = nowMs();
          await upsertCacheRow(c.env.DB, {
            key,
            type,
            size: Number.isFinite(contentLength) ? contentLength : byteCount,
            content_type: contentType,
            created_at: now,
            last_access_at: now,
            expires_at: expiresAt * 1000,
          });
        } catch {
          // ignore write errors
        }
      })(),
    );

    const outHeaders = new Headers(upstream.headers);
    outHeaders.set("Access-Control-Allow-Origin", "*");
    outHeaders.set("Cache-Control", `public, max-age=${cacheSeconds}`);
    if (contentType) outHeaders.set("Content-Type", contentType);
    return new Response(toClient, { status: upstream.status, headers: outHeaders });
  }

  const outHeaders = new Headers(upstream.headers);
  outHeaders.set("Access-Control-Allow-Origin", "*");
  outHeaders.set("Cache-Control", `public, max-age=${cacheSeconds}`);
  if (contentType) outHeaders.set("Content-Type", contentType);
  return new Response(upstream.body, { status: upstream.status, headers: outHeaders });
});
