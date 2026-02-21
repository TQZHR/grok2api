export interface TokenUsageCounts {
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cached_tokens: number;
  input_tokens_details: { text_tokens: number; image_tokens: number };
  output_tokens_details: { text_tokens: number; reasoning_tokens: number };
}

export function estimateTokens(text: string): number {
  const raw = String(text || "");
  if (!raw) return 0;
  let ascii = 0;
  let nonAscii = 0;
  for (const ch of raw) {
    if (ch.charCodeAt(0) <= 0x7f) ascii += 1;
    else nonAscii += 1;
  }
  const asciiTokens = Math.ceil(ascii / 4);
  return asciiTokens + nonAscii;
}

export function splitThinkSegments(text: string): { reasoningText: string; outputText: string } {
  const raw = String(text || "");
  if (!raw) return { reasoningText: "", outputText: "" };
  const regex = /<think>([\s\S]*?)<\/think>/gi;
  const reasoningParts: string[] = [];
  let output = raw;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(raw))) {
    if (match[1]) reasoningParts.push(match[1]);
  }
  output = output.replace(regex, "");
  return { reasoningText: reasoningParts.join("\n"), outputText: output };
}

export function estimateInputTokensFromMessages(messages: Array<{ content?: unknown }>): {
  textTokens: number;
  imageTokens: number;
  promptTokens: number;
} {
  const parts: string[] = [];
  let imageTokens = 0;
  for (const msg of messages || []) {
    const content = (msg as any)?.content;
    if (Array.isArray(content)) {
      for (const item of content) {
        if (item?.type === "text" && typeof item.text === "string") parts.push(item.text);
        if (item?.type === "image_url") imageTokens += 0;
      }
    } else if (typeof content === "string") {
      parts.push(content);
    }
  }
  const textTokens = estimateTokens(parts.join("\n"));
  return { textTokens, imageTokens, promptTokens: textTokens + imageTokens };
}

export function buildChatUsageFromTexts(args: {
  promptTextTokens: number;
  promptImageTokens: number;
  completionText: string;
}): TokenUsageCounts {
  const { reasoningText, outputText } = splitThinkSegments(args.completionText);
  const completionTextTokens = estimateTokens(outputText);
  const reasoningTokens = estimateTokens(reasoningText);
  const outputTokens = completionTextTokens + reasoningTokens;
  const inputTokens = args.promptTextTokens + args.promptImageTokens;
  const totalTokens = inputTokens + outputTokens;
  return {
    total_tokens: totalTokens,
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    reasoning_tokens: reasoningTokens,
    cached_tokens: 0,
    input_tokens_details: { text_tokens: args.promptTextTokens, image_tokens: args.promptImageTokens },
    output_tokens_details: { text_tokens: completionTextTokens, reasoning_tokens: reasoningTokens },
  };
}

export function buildImageUsageFromPrompt(prompt: string): TokenUsageCounts {
  const inputTokens = estimateTokens(prompt || "");
  return {
    total_tokens: inputTokens,
    input_tokens: inputTokens,
    output_tokens: 0,
    reasoning_tokens: 0,
    cached_tokens: 0,
    input_tokens_details: { text_tokens: inputTokens, image_tokens: 0 },
    output_tokens_details: { text_tokens: 0, reasoning_tokens: 0 },
  };
}