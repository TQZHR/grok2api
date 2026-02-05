import { Hono } from "hono";
import { cors } from "hono/cors";
import type { Env } from "../env";
import { requireApiAuth } from "../auth";
import { getSettings, normalizeCfCookie } from "../settings";
import { isValidModel, MODEL_CONFIG } from "../grok/models";
import { extractContent, buildConversationPayload, sendConversationRequest } from "../grok/conversation";
import { uploadImage } from "../grok/upload";
import { createMediaPost, createPost } from "../grok/create";
import { createOpenAiStreamFromGrokNdjson, parseOpenAiFromGrokNdjson } from "../grok/processor";
import { addRequestLog } from "../repo/logs";
import { applyCooldown, recordTokenFailure, selectBestToken } from "../repo/tokens";
import type { ApiAuthInfo } from "../auth";
import { getApiKeyLimits } from "../repo/apiKeys";
import { localDayString, tryConsumeDailyUsage, tryConsumeDailyUsageMulti } from "../repo/apiKeyUsage";
import { nextLocalMidnightExpirationSeconds } from "../kv/cleanup";
import { nowMs } from "../utils/time";
import { upsertCacheRow } from "../repo/cache";

function openAiError(message: string, code: string): Record<string, unknown> {
  return { error: { message, type: "invalid_request_error", code } };
}

function getClientIp(req: Request): string {
  return (
    req.headers.get("CF-Connecting-IP") ||
    req.headers.get("X-Forwarded-For")?.split(",")[0]?.trim() ||
    "0.0.0.0"
  );
}

async function mapLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<R[]> {
  const results: R[] = [];
  const queue = items.slice();
  const workers = Array.from({ length: Math.max(1, limit) }, async () => {
    while (queue.length) {
      const item = queue.shift() as T;
      results.push(await fn(item));
    }
  });
  await Promise.all(workers);
  return results;
}

export const openAiRoutes = new Hono<{ Bindings: Env; Variables: { apiAuth: ApiAuthInfo } }>();

openAiRoutes.use(
  "/*",
  cors({
    origin: "*",
    allowHeaders: ["Authorization", "Content-Type"],
    allowMethods: ["GET", "POST", "OPTIONS"],
    maxAge: 86400,
  }),
);

openAiRoutes.use("/*", requireApiAuth);

function parseIntSafe(v: string | undefined, fallback: number): number {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.floor(n);
}

function quotaError(bucket: string): Record<string, unknown> {
  return openAiError(`Daily quota exceeded: ${bucket}`, "daily_quota_exceeded");
}

async function enforceQuota(args: {
  env: Env;
  apiAuth: ApiAuthInfo;
  model: string;
  kind: "chat" | "image" | "video";
  imageCount?: number;
}): Promise<{ ok: true } | { ok: false; resp: Response }> {
  const key = args.apiAuth.key;
  if (!key) return { ok: true };
  if (args.apiAuth.is_admin) return { ok: true };

  const limits = await getApiKeyLimits(args.env.DB, key);
  if (!limits) return { ok: true };

  const tz = parseIntSafe(args.env.CACHE_RESET_TZ_OFFSET_MINUTES, 480);
  const day = localDayString(nowMs(), tz);
  const atMs = nowMs();
  const jsonHeaders = { "content-type": "application/json; charset=utf-8" };

  if (args.model === "grok-4-heavy") {
    const ok = await tryConsumeDailyUsageMulti({
      db: args.env.DB,
      key,
      day,
      atMs,
      updates: [
        { field: "heavy_used", inc: 1, limit: limits.heavy_limit },
        { field: "chat_used", inc: 1, limit: limits.chat_limit },
      ],
    });
    if (!ok) return { ok: false, resp: new Response(JSON.stringify(quotaError("heavy/chat")), { status: 429, headers: jsonHeaders }) };
    return { ok: true };
  }

  if (args.kind === "video") {
    const ok = await tryConsumeDailyUsage({
      db: args.env.DB,
      key,
      day,
      atMs,
      field: "video_used",
      inc: 1,
      limit: limits.video_limit,
    });
    if (!ok) return { ok: false, resp: new Response(JSON.stringify(quotaError("video")), { status: 429, headers: jsonHeaders }) };
    return { ok: true };
  }

  if (args.kind === "image") {
    const inc = Math.max(1, Math.floor(Number(args.imageCount ?? 1) || 1));
    const ok = await tryConsumeDailyUsage({
      db: args.env.DB,
      key,
      day,
      atMs,
      field: "image_used",
      inc,
      limit: limits.image_limit,
    });
    if (!ok) return { ok: false, resp: new Response(JSON.stringify(quotaError("image")), { status: 429, headers: jsonHeaders }) };
    return { ok: true };
  }

  // chat
  const ok = await tryConsumeDailyUsage({
    db: args.env.DB,
    key,
    day,
    atMs,
    field: "chat_used",
    inc: 1,
    limit: limits.chat_limit,
  });
  if (!ok) return { ok: false, resp: new Response(JSON.stringify(quotaError("chat")), { status: 429, headers: jsonHeaders }) };
  return { ok: true };
}

function base64UrlEncodeString(input: string): string {
  const bytes = new TextEncoder().encode(input);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function encodeAssetPath(raw: string): string {
  try {
    const u = new URL(raw);
    return `u_${base64UrlEncodeString(u.toString())}`;
  } catch {
    const p = raw.startsWith("/") ? raw : `/${raw}`;
    return `p_${base64UrlEncodeString(p)}`;
  }
}

function toProxyUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, "")}/images/${path}`;
}

async function collectGeneratedImageUrls(resp: Response): Promise<string[]> {
  const text = await resp.text();
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  for (const line of lines) {
    let data: any;
    try {
      data = JSON.parse(line);
    } catch {
      continue;
    }
    const err = data?.error;
    if (err?.message) throw new Error(String(err.message));
    const grok = data?.result?.response;
    const urls = grok?.modelResponse?.generatedImageUrls;
    if (Array.isArray(urls)) {
      return urls.filter((u: any) => typeof u === "string" && u.trim() && u.trim() !== "/").map((u: string) => u.trim());
    }
  }
  return [];
}

openAiRoutes.get("/models", async (c) => {
  const ts = Math.floor(Date.now() / 1000);
  const data = Object.entries(MODEL_CONFIG).map(([id, cfg]) => ({
    id,
    object: "model",
    created: ts,
    owned_by: "x-ai",
    display_name: cfg.display_name,
    description: cfg.description,
    raw_model_path: cfg.raw_model_path,
    default_temperature: cfg.default_temperature,
    default_max_output_tokens: cfg.default_max_output_tokens,
    supported_max_output_tokens: cfg.supported_max_output_tokens,
    default_top_p: cfg.default_top_p,
  }));
  return c.json({ object: "list", data });
});

openAiRoutes.get("/models/:modelId", async (c) => {
  const modelId = c.req.param("modelId");
  if (!isValidModel(modelId)) return c.json(openAiError(`Model '${modelId}' not found`, "model_not_found"), 404);
  const cfg = MODEL_CONFIG[modelId]!;
  const ts = Math.floor(Date.now() / 1000);
  return c.json({
    id: modelId,
    object: "model",
    created: ts,
    owned_by: "x-ai",
    display_name: cfg.display_name,
    description: cfg.description,
    raw_model_path: cfg.raw_model_path,
    default_temperature: cfg.default_temperature,
    default_max_output_tokens: cfg.default_max_output_tokens,
    supported_max_output_tokens: cfg.supported_max_output_tokens,
    default_top_p: cfg.default_top_p,
  });
});

openAiRoutes.post("/chat/completions", async (c) => {
  const start = Date.now();
  const ip = getClientIp(c.req.raw);
  const keyName = c.get("apiAuth").name ?? "Unknown";

  const origin = new URL(c.req.url).origin;

  let requestedModel = "";
  try {
    const body = (await c.req.json()) as {
      model?: string;
      messages?: any[];
      stream?: boolean;
      video_config?: {
        aspect_ratio?: string;
        video_length?: number;
        resolution?: string;
        preset?: string;
      };
    };

    requestedModel = String(body.model ?? "");
    if (!requestedModel) return c.json(openAiError("Missing 'model'", "missing_model"), 400);
    if (!Array.isArray(body.messages)) return c.json(openAiError("Missing 'messages'", "missing_messages"), 400);
    if (!isValidModel(requestedModel))
      return c.json(openAiError(`Model '${requestedModel}' not supported`, "model_not_supported"), 400);

    const settingsBundle = await getSettings(c.env);
    const cfg = MODEL_CONFIG[requestedModel]!;

    const retryCodes = Array.isArray(settingsBundle.grok.retry_status_codes)
      ? settingsBundle.grok.retry_status_codes
      : [401, 429];

    const stream = Boolean(body.stream);
    const maxRetry = 3;
    let lastErr: string | null = null;

    // === Quota check (best-effort) ===
    // - heavy: consumes both heavy + chat
    // - image model: counts as 2 images per request (grok upstream emits up to 2)
    // - video model: 1 video per request
    // - others: 1 chat per request
    const quotaKind = cfg.is_video_model ? "video" : cfg.is_image_model ? "image" : "chat";
    const quota = await enforceQuota({
      env: c.env,
      apiAuth: c.get("apiAuth"),
      model: requestedModel,
      kind: quotaKind as any,
      ...(cfg.is_image_model ? { imageCount: 2 } : {}),
    });
    if (!quota.ok) return quota.resp;

    for (let attempt = 0; attempt < maxRetry; attempt++) {
      const chosen = await selectBestToken(c.env.DB, requestedModel);
      if (!chosen) return c.json(openAiError("No available token", "NO_AVAILABLE_TOKEN"), 503);

      const jwt = chosen.token;
      const cf = normalizeCfCookie(settingsBundle.grok.cf_clearance ?? "");
      const cookie = cf ? `sso-rw=${jwt};sso=${jwt};${cf}` : `sso-rw=${jwt};sso=${jwt}`;

      const { content, images } = extractContent(body.messages as any);
      const isVideoModel = Boolean(cfg.is_video_model);
      const imgInputs = isVideoModel && images.length > 1 ? images.slice(0, 1) : images;

      try {
        const uploads = await mapLimit(imgInputs, 5, (u) => uploadImage(u, cookie, settingsBundle.grok));
        const imgIds = uploads.map((u) => u.fileId).filter(Boolean);
        const imgUris = uploads.map((u) => u.fileUri).filter(Boolean);

        let postId: string | undefined;
        if (isVideoModel) {
          if (imgUris.length) {
            const post = await createPost(imgUris[0]!, cookie, settingsBundle.grok);
            postId = post.postId || undefined;
          } else {
            const post = await createMediaPost(
              { mediaType: "MEDIA_POST_TYPE_VIDEO", prompt: content },
              cookie,
              settingsBundle.grok,
            );
            postId = post.postId || undefined;
          }
        }

        const { payload, referer } = buildConversationPayload({
          requestModel: requestedModel,
          content,
          imgIds,
          imgUris,
          ...(postId ? { postId } : {}),
          ...(isVideoModel && body.video_config ? { videoConfig: body.video_config } : {}),
          settings: settingsBundle.grok,
        });

        const upstream = await sendConversationRequest({
          payload,
          cookie,
          settings: settingsBundle.grok,
          ...(referer ? { referer } : {}),
        });

        if (!upstream.ok) {
          const txt = await upstream.text().catch(() => "");
          lastErr = `Upstream ${upstream.status}: ${txt.slice(0, 200)}`;
          await recordTokenFailure(c.env.DB, jwt, upstream.status, txt.slice(0, 200));
          await applyCooldown(c.env.DB, jwt, upstream.status);
          if (retryCodes.includes(upstream.status) && attempt < maxRetry - 1) continue;
          break;
        }

        if (stream) {
          const sse = createOpenAiStreamFromGrokNdjson(upstream, {
            cookie,
            settings: settingsBundle.grok,
            global: settingsBundle.global,
            origin,
            onFinish: async ({ status, duration }) => {
              await addRequestLog(c.env.DB, {
                ip,
                model: requestedModel,
                duration: Number(duration.toFixed(2)),
                status,
                key_name: keyName,
                token_suffix: jwt.slice(-6),
                error: status === 200 ? "" : "stream_error",
              });
            },
          });

          return new Response(sse, {
            status: 200,
            headers: {
              "Content-Type": "text/event-stream; charset=utf-8",
              "Cache-Control": "no-cache",
              Connection: "keep-alive",
              "X-Accel-Buffering": "no",
              "Access-Control-Allow-Origin": "*",
            },
          });
        }

        const json = await parseOpenAiFromGrokNdjson(upstream, {
          cookie,
          settings: settingsBundle.grok,
          global: settingsBundle.global,
          origin,
          requestedModel,
        });

        const duration = (Date.now() - start) / 1000;
        await addRequestLog(c.env.DB, {
          ip,
          model: requestedModel,
          duration: Number(duration.toFixed(2)),
          status: 200,
          key_name: keyName,
          token_suffix: jwt.slice(-6),
          error: "",
        });

        return c.json(json);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        lastErr = msg;
        await recordTokenFailure(c.env.DB, jwt, 500, msg);
        await applyCooldown(c.env.DB, jwt, 500);
        if (attempt < maxRetry - 1) continue;
      }
    }

    const duration = (Date.now() - start) / 1000;
    await addRequestLog(c.env.DB, {
      ip,
      model: requestedModel,
      duration: Number(duration.toFixed(2)),
      status: 500,
      key_name: keyName,
      token_suffix: "",
      error: lastErr ?? "unknown_error",
    });

    return c.json(openAiError(lastErr ?? "Upstream error", "upstream_error"), 500);
  } catch (e) {
    const duration = (Date.now() - start) / 1000;
    await addRequestLog(c.env.DB, {
      ip,
      model: requestedModel || "unknown",
      duration: Number(duration.toFixed(2)),
      status: 500,
      key_name: keyName,
      token_suffix: "",
      error: e instanceof Error ? e.message : String(e),
    });
    return c.json(openAiError("Internal error", "internal_error"), 500);
  }
});

openAiRoutes.post("/images/generations", async (c) => {
  const start = Date.now();
  const ip = getClientIp(c.req.raw);
  const keyName = c.get("apiAuth").name ?? "Unknown";
  const origin = new URL(c.req.url).origin;

  let requestedModel = "grok-imagine-1.0";
  try {
    const body = (await c.req.json()) as { prompt?: string; model?: string; n?: number };
    const prompt = String(body.prompt ?? "").trim();
    if (!prompt) return c.json(openAiError("Missing 'prompt'", "missing_prompt"), 400);

    requestedModel = String(body.model ?? "grok-imagine-1.0").trim() || "grok-imagine-1.0";
    if (!isValidModel(requestedModel))
      return c.json(openAiError(`Model '${requestedModel}' not supported`, "model_not_supported"), 400);
    const cfg = MODEL_CONFIG[requestedModel]!;
    if (!cfg.is_image_model) return c.json(openAiError(`Model '${requestedModel}' is not an image model`, "invalid_model"), 400);

    const nRaw = Number(body.n ?? 1);
    const n = Number.isFinite(nRaw) ? Math.max(1, Math.min(10, Math.floor(nRaw))) : 1;

    const quota = await enforceQuota({
      env: c.env,
      apiAuth: c.get("apiAuth"),
      model: requestedModel,
      kind: "image",
      imageCount: n,
    });
    if (!quota.ok) return quota.resp;

    const settingsBundle = await getSettings(c.env);
    const cf = normalizeCfCookie(settingsBundle.grok.cf_clearance ?? "");

    const calls = Math.ceil(n / 2);
    const baseUrl = (settingsBundle.global.base_url ?? "").trim() || origin;

    const doOne = async (): Promise<string[]> => {
      const chosen = await selectBestToken(c.env.DB, requestedModel);
      if (!chosen) throw new Error("No available token");
      const cookie = cf ? `sso-rw=${chosen.token};sso=${chosen.token};${cf}` : `sso-rw=${chosen.token};sso=${chosen.token}`;
      const { payload, referer } = buildConversationPayload({
        requestModel: requestedModel,
        content: `Image Generation:${prompt}`,
        imgIds: [],
        imgUris: [],
        settings: settingsBundle.grok,
      });
      const upstream = await sendConversationRequest({
        payload,
        cookie,
        settings: settingsBundle.grok,
        ...(referer ? { referer } : {}),
      });
      if (!upstream.ok) {
        const txt = await upstream.text().catch(() => "");
        await recordTokenFailure(c.env.DB, chosen.token, upstream.status, txt.slice(0, 200));
        await applyCooldown(c.env.DB, chosen.token, upstream.status);
        throw new Error(`Upstream ${upstream.status}: ${txt.slice(0, 200)}`);
      }
      const rawUrls = await collectGeneratedImageUrls(upstream);
      return rawUrls.map((u) => toProxyUrl(baseUrl, encodeAssetPath(u)));
    };

    const urlsNested = await mapLimit(Array.from({ length: calls }).map((_, i) => i), 3, async () => doOne());
    const urls = urlsNested.flat().filter(Boolean);
    const selected = urls.slice(0, n);

    const duration = (Date.now() - start) / 1000;
    await addRequestLog(c.env.DB, {
      ip,
      model: requestedModel,
      duration: Number(duration.toFixed(2)),
      status: 200,
      key_name: keyName,
      token_suffix: "",
      error: "",
    });

    return c.json({
      created: Math.floor(Date.now() / 1000),
      data: selected.map((url) => ({ url })),
    });
  } catch (e) {
    const duration = (Date.now() - start) / 1000;
    await addRequestLog(c.env.DB, {
      ip,
      model: requestedModel || "image",
      duration: Number(duration.toFixed(2)),
      status: 500,
      key_name: keyName,
      token_suffix: "",
      error: e instanceof Error ? e.message : String(e),
    });
    return c.json(openAiError(e instanceof Error ? e.message : "Internal error", "internal_error"), 500);
  }
});

openAiRoutes.post("/uploads/image", async (c) => {
  try {
    const form = await c.req.formData();
    const file = form.get("file");
    if (!(file instanceof File)) return c.json(openAiError("Missing file", "missing_file"), 400);

    const mime = String(file.type || "application/octet-stream");
    if (!mime.toLowerCase().startsWith("image/"))
      return c.json(openAiError(`Unsupported mime: ${mime}`, "unsupported_file"), 400);

    const bytes = await file.arrayBuffer();
    const size = bytes.byteLength;
    const maxBytes = Math.min(25 * 1024 * 1024, Math.max(1, parseIntSafe(c.env.KV_CACHE_MAX_BYTES, 25 * 1024 * 1024)));
    if (size > maxBytes) return c.json(openAiError(`File too large (${size} > ${maxBytes})`, "file_too_large"), 413);

    const ext = (() => {
      const m = mime.toLowerCase();
      if (m === "image/png") return "png";
      if (m === "image/webp") return "webp";
      if (m === "image/gif") return "gif";
      if (m === "image/jpeg" || m === "image/jpg") return "jpg";
      return "jpg";
    })();

    const name = `upload-${crypto.randomUUID()}.${ext}`;
    const kvKey = `image/${name}`;

    const tz = parseIntSafe(c.env.CACHE_RESET_TZ_OFFSET_MINUTES, 480);
    const expiresAt = nextLocalMidnightExpirationSeconds(nowMs(), tz);

    await c.env.KV_CACHE.put(kvKey, bytes, {
      expiration: expiresAt,
      metadata: { contentType: mime, size },
    });

    const now = nowMs();
    await upsertCacheRow(c.env.DB, {
      key: kvKey,
      type: "image",
      size,
      content_type: mime,
      created_at: now,
      last_access_at: now,
      expires_at: expiresAt * 1000,
    });

    return c.json({
      url: `/images/${encodeURIComponent(name)}`,
      name,
      size_bytes: size,
    });
  } catch (e) {
    return c.json(openAiError(e instanceof Error ? e.message : "Internal error", "internal_error"), 500);
  }
});

openAiRoutes.options("/*", (c) => c.body(null, 204));
