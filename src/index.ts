import { Hono } from "hono";
import type { Env } from "./env";
import { openAiRoutes } from "./routes/openai";
import { mediaRoutes } from "./routes/media";
import { adminRoutes } from "./routes/admin";
import { runKvDailyClear } from "./kv/cleanup";

const app = new Hono<{ Bindings: Env }>();

function getAssets(env: Env): Fetcher | null {
  const anyEnv = env as unknown as { ASSETS?: unknown };
  const assets = anyEnv.ASSETS as { fetch?: unknown } | undefined;
  return assets && typeof assets.fetch === "function" ? (assets as Fetcher) : null;
}

function getBuildSha(env: Env): string {
  const v = String((env as any)?.BUILD_SHA ?? "").trim();
  return v || "dev";
}

function isDebugRequest(c: any): boolean {
  try {
    return new URL(c.req.url).searchParams.get("debug") === "1";
  } catch {
    return false;
  }
}

function withResponseHeaders(res: Response, extra: Record<string, string>): Response {
  const headers = new Headers(res.headers);
  for (const [k, v] of Object.entries(extra)) headers.set(k, v);
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
}

function assetFetchError(message: string, buildSha: string): Response {
  return new Response(message, {
    status: 500,
    headers: { "content-type": "text/plain; charset=utf-8", "x-grok2api-build": buildSha },
  });
}

async function fetchAsset(c: any, pathname: string): Promise<Response> {
  const assets = getAssets(c.env as Env);
  const buildSha = getBuildSha(c.env as Env);
  if (!assets) {
    console.error("ASSETS binding missing: check wrangler.toml assets binding");
    return assetFetchError(
      'Internal Server Error: missing ASSETS binding. Check `wrangler.toml` `assets = { directory = \"./app/static\", binding = \"ASSETS\" }` and redeploy.',
      buildSha,
    );
  }

  const url = new URL(c.req.url);
  url.pathname = pathname;
  try {
    const res = await assets.fetch(new Request(url.toString(), c.req.raw));
    const extra: Record<string, string> = { "x-grok2api-build": buildSha };

    // Avoid caching UI files aggressively, otherwise users may keep seeing old UI after redeploy.
    // We keep images/videos cacheable (handled by KV + cache proxy paths), but HTML/JS/CSS should refresh quickly.
    const lower = pathname.toLowerCase();
    if (lower.endsWith(".html") || lower.endsWith(".js") || lower.endsWith(".css")) {
      extra["cache-control"] = "no-store, no-cache, must-revalidate";
      extra["pragma"] = "no-cache";
      extra["expires"] = "0";
    }

    return withResponseHeaders(res, extra);
  } catch (err) {
    console.error(`ASSETS fetch failed (${pathname}):`, err);
    const detail = isDebugRequest(c) ? `\n\n${err instanceof Error ? err.stack || err.message : String(err)}` : "";
    return assetFetchError(`Internal Server Error: failed to fetch asset ${pathname}.${detail}`, buildSha);
  }
}

app.onError((err, c) => {
  console.error("Unhandled error:", err);
  const buildSha = getBuildSha(c.env as Env);
  const detail = isDebugRequest(c) ? `\n\n${err instanceof Error ? err.stack || err.message : String(err)}` : "";
  const res = c.text(`Internal Server Error${detail}`, 500);
  return withResponseHeaders(res, { "x-grok2api-build": buildSha });
});

app.route("/v1", openAiRoutes);
app.route("/", mediaRoutes);
app.route("/", adminRoutes);

// Backward-compatible local-cache viewer URLs used by the multi-page admin UI.
// In Workers we serve cache via /images/*, so redirect /v1/files/* to /images/*.
app.get("/v1/files/image/:imgPath{.+}", (c) =>
  c.redirect(`/images/${encodeURIComponent(c.req.param("imgPath"))}`, 302),
);
app.get("/v1/files/video/:imgPath{.+}", (c) =>
  c.redirect(`/images/${encodeURIComponent(c.req.param("imgPath"))}`, 302),
);

app.get("/_worker.js", (c) => c.notFound());

app.get("/", (c) => c.redirect("/login", 302));

app.get("/login", (c) => {
  const buildSha = getBuildSha(c.env as Env);
  const v = c.req.query("v") ?? "";
  if (v !== buildSha) return c.redirect(`/login?v=${encodeURIComponent(buildSha)}`, 302);
  return fetchAsset(c, "/login/login.html");
});

// Legacy (old admin UI): keep /manage as an alias.
app.get("/manage", (c) => {
  const buildSha = getBuildSha(c.env as Env);
  const v = c.req.query("v") ?? "";
  if (v !== buildSha) return c.redirect(`/admin/token?v=${encodeURIComponent(buildSha)}`, 302);
  return c.redirect(`/admin/token?v=${encodeURIComponent(buildSha)}`, 302);
});

app.get("/admin", (c) => c.redirect("/login", 302));

app.get("/admin/token", (c) => {
  const buildSha = getBuildSha(c.env as Env);
  const v = c.req.query("v") ?? "";
  if (v !== buildSha) return c.redirect(`/admin/token?v=${encodeURIComponent(buildSha)}`, 302);
  return fetchAsset(c, "/token/token.html");
});

app.get("/admin/datacenter", (c) => {
  const buildSha = getBuildSha(c.env as Env);
  const v = c.req.query("v") ?? "";
  if (v !== buildSha) return c.redirect(`/admin/datacenter?v=${encodeURIComponent(buildSha)}`, 302);
  return fetchAsset(c, "/datacenter/datacenter.html");
});

app.get("/admin/config", (c) => {
  const buildSha = getBuildSha(c.env as Env);
  const v = c.req.query("v") ?? "";
  if (v !== buildSha) return c.redirect(`/admin/config?v=${encodeURIComponent(buildSha)}`, 302);
  return fetchAsset(c, "/config/config.html");
});

app.get("/admin/cache", (c) => {
  const buildSha = getBuildSha(c.env as Env);
  const v = c.req.query("v") ?? "";
  if (v !== buildSha) return c.redirect(`/admin/cache?v=${encodeURIComponent(buildSha)}`, 302);
  return fetchAsset(c, "/cache/cache.html");
});

app.get("/static/*", (c) => {
  const url = new URL(c.req.url);
  if (url.pathname === "/static/_worker.js") return c.notFound();
  url.pathname = url.pathname.replace(/^\/static\//, "/");
  return fetchAsset(c, url.pathname);
});

app.get("/health", (c) =>
  c.json({
    status: "healthy",
    service: "Grok2API",
    runtime: "cloudflare-workers",
    build: { sha: getBuildSha(c.env as Env) },
    bindings: {
      db: Boolean((c.env as any)?.DB),
      kv_cache: Boolean((c.env as any)?.KV_CACHE),
      assets: Boolean(getAssets(c.env as any)),
    },
  }),
);

app.notFound(async (c) => {
  const assets = getAssets(c.env as any);
  const buildSha = getBuildSha(c.env as Env);
  // Avoid calling c.notFound() here because it will invoke this handler again.
  if (!assets) return withResponseHeaders(c.text("Not Found", 404), { "x-grok2api-build": buildSha });
  try {
    const res = await assets.fetch(c.req.raw);
    // Keep the header consistent for debugging/version checks.
    return withResponseHeaders(res, { "x-grok2api-build": buildSha });
  } catch (err) {
    console.error("ASSETS fetch failed (notFound):", err);
    const detail = isDebugRequest(c) ? `\n\n${err instanceof Error ? err.stack || err.message : String(err)}` : "";
    return withResponseHeaders(c.text(`Internal Server Error${detail}`, 500), { "x-grok2api-build": buildSha });
  }
});

const handler: ExportedHandler<Env> = {
  fetch: (request, env, ctx) => app.fetch(request, env, ctx),
  scheduled: (_event, env, ctx) => {
    ctx.waitUntil(runKvDailyClear(env));
  },
};

export default handler;
