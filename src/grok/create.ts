import type { GrokSettings } from "../settings";
import { getDynamicHeaders } from "./headers";

const ENDPOINT = "https://grok.com/rest/media/post/create";

export type MediaPostType = "MEDIA_POST_TYPE_VIDEO" | "MEDIA_POST_TYPE_IMAGE";

export async function createMediaPost(
  args: { mediaType: MediaPostType; prompt?: string; mediaUrl?: string },
  cookie: string,
  settings: GrokSettings,
): Promise<{ postId: string }> {
  const headers = getDynamicHeaders(settings, "/rest/media/post/create");
  headers.Cookie = cookie;
  headers.Referer = "https://grok.com/imagine";

  const bodyObj: Record<string, unknown> = { mediaType: args.mediaType };
  if (args.mediaType === "MEDIA_POST_TYPE_IMAGE") {
    if (!args.mediaUrl) throw new Error("缺少 mediaUrl");
    bodyObj.mediaUrl = args.mediaUrl;
  } else {
    if (!args.prompt) throw new Error("缺少 prompt");
    bodyObj.prompt = args.prompt;
  }

  const body = JSON.stringify(bodyObj);

  const resp = await fetch(ENDPOINT, { method: "POST", headers, body });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`创建会话失败: ${resp.status} ${text.slice(0, 200)}`);
  }

  const data = (await resp.json()) as { post?: { id?: string } };
  return { postId: data.post?.id ?? "" };
}

export async function createPost(
  fileUri: string,
  cookie: string,
  settings: GrokSettings,
): Promise<{ postId: string }> {
  const path = fileUri.startsWith("/") ? fileUri : `/${fileUri}`;
  const url = `https://assets.grok.com${path}`;
  return createMediaPost({ mediaType: "MEDIA_POST_TYPE_IMAGE", mediaUrl: url }, cookie, settings);
}

