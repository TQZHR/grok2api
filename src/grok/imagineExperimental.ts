import { sendConversationRequest } from "./conversation";
import { getDynamicHeaders } from "./headers";
import type { GrokSettings } from "../settings";

export const IMAGE_METHOD_LEGACY = "legacy" as const;
export const IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL = "imagine_ws_experimental" as const;
const IMAGE_METHOD_ALIASES: Record<string, ImageGenerationMethod> = {
  imagine_ws: IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL,
  experimental: IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL,
  new: IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL,
  new_method: IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL,
};

export type ImageGenerationMethod =
  | typeof IMAGE_METHOD_LEGACY
  | typeof IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL;

const IMAGINE_WS_HTTP_API = "https://grok.com/ws/imagine/listen";
const IMAGINE_REFERER = "https://grok.com/imagine";
const ASSET_API = "https://assets.grok.com";

type WsJson = Record<string, unknown>;

export function resolveImageGenerationMethod(raw: unknown): ImageGenerationMethod {
  const value = String(raw ?? "")
    .trim()
    .toLowerCase();
  if (value === IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL) return IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL;
  if (IMAGE_METHOD_ALIASES[value]) return IMAGE_METHOD_ALIASES[value];
  return IMAGE_METHOD_LEGACY;
}

const ALLOWED_ASPECT_RATIOS = new Set(["16:9", "9:16", "1:1", "2:3", "3:2"]);
const SIZE_TO_RATIO: Record<string, string> = {
  "1024x1024": "1:1",
  "512x512": "1:1",
  "1024x576": "16:9",
  "1280x720": "16:9",
  "1536x864": "16:9",
  "576x1024": "9:16",
  "720x1280": "9:16",
  "864x1536": "9:16",
  "1024x1536": "2:3",
  "512x768": "2:3",
  "768x1024": "2:3",
  "1536x1024": "3:2",
  "768x512": "3:2",
  "1024x768": "3:2",
};

export function resolveAspectRatio(size: unknown): string {
  const value = String(size ?? "")
    .trim()
    .toLowerCase();
  if (ALLOWED_ASPECT_RATIOS.has(value)) return value;
  return SIZE_TO_RATIO[value] ?? "2:3";
}

export interface ImagineWsProgress {
  index: number;
  progress: number;
}

export interface ImagineWsCompleted {
  index: number;
  url: string;
}

function clampProgress(input: unknown): number | null {
  const n = Number(input);
  if (!Number.isFinite(n)) return null;
  if (n <= 0) return 0;
  if (n >= 100) return 100;
  return n;
}

function normalizeAssetUrl(raw: string): string {
  const value = String(raw ?? "").trim();
  if (!value) return "";
  if (value.startsWith("http://") || value.startsWith("https://")) return value;
  return `${ASSET_API}/${value.replace(/^\/+/, "")}`;
}

function decodeWsData(data: unknown): string {
  if (typeof data === "string") return data;
  if (data instanceof ArrayBuffer) return new TextDecoder().decode(data);
  if (ArrayBuffer.isView(data)) {
    return new TextDecoder().decode(
      new Uint8Array(data.buffer, data.byteOffset, data.byteLength),
    );
  }
  return "";
}

function parseWsJson(data: unknown): WsJson | null {
  const raw = decodeWsData(data);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as WsJson;
    }
  } catch {
    // ignore malformed message
  }
  return null;
}

function extractProgress(msg: WsJson): number | null {
  return (
    clampProgress(msg.percentage_complete) ??
    clampProgress(msg.percentageComplete) ??
    clampProgress(msg.progress)
  );
}

function extractUrl(msg: WsJson): string {
  for (const key of ["url", "imageUrl", "image_url"] as const) {
    const value = msg[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function isCompleted(msg: WsJson, progress: number | null): boolean {
  const status = String(msg.current_status ?? msg.currentStatus ?? "")
    .trim()
    .toLowerCase();
  if (status === "completed" || status === "done" || status === "success") return true;
  return progress !== null && progress >= 100;
}

function buildImagineWsPayload(prompt: string, requestId: string, aspectRatio: string): WsJson {
  return {
    type: "conversation.item.create",
    timestamp: Date.now(),
    item: {
      type: "message",
      content: [
        {
          requestId,
          text: prompt,
          type: "input_scroll",
          properties: {
            section_count: 0,
            is_kids_mode: false,
            enable_nsfw: true,
            skip_upsampler: false,
            is_initial: false,
            aspect_ratio: aspectRatio,
          },
        },
      ],
    },
  };
}

export async function generateImagineWs(args: {
  prompt: string;
  n: number;
  cookie: string;
  settings: GrokSettings;
  timeoutMs?: number;
  aspectRatio?: string;
  progressCb?: (progress: ImagineWsProgress) => void | Promise<void>;
  completedCb?: (completed: ImagineWsCompleted) => void | Promise<void>;
}): Promise<string[]> {
  const timeoutMs = Math.max(10_000, Number(args.timeoutMs ?? 120_000));
  const targetCount = Math.max(1, Math.floor(Number(args.n || 1)));
  const aspectRatio = resolveAspectRatio(args.aspectRatio);
  const requestId = crypto.randomUUID();

  const headers = getDynamicHeaders(args.settings, "/ws/imagine/listen");
  headers.Cookie = args.cookie;
  headers.Origin = "https://grok.com";
  headers.Referer = IMAGINE_REFERER;
  headers.Connection = "Upgrade";
  headers.Upgrade = "websocket";
  delete headers["Content-Type"];

  const wsResp = await fetch(IMAGINE_WS_HTTP_API, { method: "GET", headers });
  const ws = wsResp.webSocket;
  if (wsResp.status !== 101 || !ws) {
    const text = await wsResp.text().catch(() => "");
    throw new Error(`Imagine websocket connect failed: ${wsResp.status} ${text.slice(0, 200)}`);
  }

  ws.accept();
  ws.send(JSON.stringify(buildImagineWsPayload(args.prompt, requestId, aspectRatio)));

  const imageIndexes = new Map<string, number>();
  const finalUrls = new Map<string, string>();

  await new Promise<void>((resolve, reject) => {
    let finished = false;

    const onMessage = (event: MessageEvent) => {
      const msg = parseWsJson(event.data);
      if (!msg) return;

      const msgRequestId = String(msg.request_id ?? msg.requestId ?? "");
      if (msgRequestId && msgRequestId !== requestId) return;

      const type = String(msg.type ?? "").toLowerCase();
      const status = String(msg.current_status ?? msg.currentStatus ?? "").toLowerCase();
      if (type === "error" || status === "error") {
        const errCode = String(msg.err_code ?? msg.errCode ?? "unknown");
        const errMessage = String(msg.err_message ?? msg.err_msg ?? msg.error ?? "unknown error");
        finish(new Error(`Imagine websocket error (${errCode}): ${errMessage}`));
        return;
      }

      const rawImageId = String(msg.id ?? msg.imageId ?? msg.image_id ?? "");
      const imageId = rawImageId || `image-${imageIndexes.size}`;
      if (!imageIndexes.has(imageId)) imageIndexes.set(imageId, imageIndexes.size);
      const imageIndex = imageIndexes.get(imageId) ?? 0;

      const progress = extractProgress(msg);
      if (progress !== null && args.progressCb) {
        Promise.resolve(args.progressCb({ index: imageIndex, progress })).catch(() => {
          // ignore callback failures
        });
      }

      const imageUrl = extractUrl(msg);
      if (imageUrl && isCompleted(msg, progress)) {
        if (!finalUrls.has(imageId)) finalUrls.set(imageId, imageUrl);
        if (args.completedCb) {
          Promise.resolve(args.completedCb({ index: imageIndex, url: imageUrl })).catch(() => {
            // ignore callback failures
          });
        }
        if (finalUrls.size >= targetCount) finish();
      }
    };

    const onClose = () => {
      if (finalUrls.size > 0) finish();
      else finish(new Error("Imagine websocket closed before completed images"));
    };

    const onError = () => {
      finish(new Error("Imagine websocket error event"));
    };

    const timer = setTimeout(() => {
      finish(new Error(`Imagine websocket timeout after ${timeoutMs}ms`));
    }, timeoutMs);

    const cleanup = () => {
      clearTimeout(timer);
      ws.removeEventListener("message", onMessage as EventListener);
      ws.removeEventListener("close", onClose as EventListener);
      ws.removeEventListener("error", onError as EventListener);
    };

    const finish = (err?: Error) => {
      if (finished) return;
      finished = true;
      cleanup();
      try {
        ws.close(1000, err ? "error" : "done");
      } catch {
        // ignore close failure
      }
      if (err) reject(err);
      else resolve();
    };

    ws.addEventListener("message", onMessage as EventListener);
    ws.addEventListener("close", onClose as EventListener);
    ws.addEventListener("error", onError as EventListener);
  });

  const urls = Array.from(finalUrls.values()).filter(Boolean);
  if (!urls.length) {
    throw new Error("Imagine websocket returned no completed images");
  }
  return urls;
}

function buildExperimentalImageEditPayload(args: {
  prompt: string;
  imageReferences: string[];
  modelName: "imagine-image-edit" | "grok-3";
}): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    temporary: true,
    modelName: args.modelName,
    message: args.prompt,
    fileAttachments: [],
    imageAttachments: [],
    disableSearch: false,
    enableImageGeneration: true,
    returnImageBytes: false,
    returnRawGrokInXaiRequest: false,
    enableImageStreaming: true,
    imageGenerationCount: 2,
    forceConcise: false,
    toolOverrides: { imageGen: true },
    enableSideBySide: true,
    sendFinalMetadata: true,
    isReasoning: false,
    disableTextFollowUps: false,
    disableMemory: false,
    forceSideBySide: false,
    isAsyncChat: false,
    responseMetadata: {
      modelConfigOverride: {
        modelMap: {
          imageEditModel: "imagine",
          imageEditModelConfig: {
            imageReferences: args.imageReferences,
          },
        },
      },
      requestModelDetails: {
        modelId: args.modelName,
      },
    },
  };

  if (args.modelName === "grok-3") {
    payload.modelMode = "MODEL_MODE_FAST";
  }

  return payload;
}

export async function sendExperimentalImageEditRequest(args: {
  prompt: string;
  fileUris: string[];
  cookie: string;
  settings: GrokSettings;
}): Promise<Response> {
  const imageReferences = args.fileUris.map((uri) => normalizeAssetUrl(uri)).filter(Boolean);
  if (!imageReferences.length) {
    throw new Error("Experimental image edit requires uploaded image references");
  }

  const payloads: Array<Record<string, unknown>> = [
    buildExperimentalImageEditPayload({
      prompt: args.prompt,
      imageReferences,
      modelName: "imagine-image-edit",
    }),
    buildExperimentalImageEditPayload({
      prompt: args.prompt,
      imageReferences,
      modelName: "grok-3",
    }),
  ];

  let lastStatus = 0;
  let lastErrorBody = "";
  for (const payload of payloads) {
    const upstream = await sendConversationRequest({
      payload,
      cookie: args.cookie,
      settings: args.settings,
      referer: IMAGINE_REFERER,
    });
    if (upstream.ok) return upstream;
    lastStatus = upstream.status;
    lastErrorBody = await upstream.text().catch(() => "");
  }

  throw new Error(
    `Experimental image edit upstream failed: ${lastStatus} ${lastErrorBody.slice(0, 200)}`,
  );
}
