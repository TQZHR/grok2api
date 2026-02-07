"""Token usage estimation utilities (no external tokenizer)."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _count_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_count = 0
    non_ascii = 0
    for ch in text:
        if ord(ch) <= 0x7F:
            ascii_count += 1
        else:
            non_ascii += 1
    return (ascii_count + 3) // 4 + non_ascii


def _split_think(text: str) -> Tuple[str, str]:
    if not text:
        return "", ""
    reasoning_parts: List[str] = []
    output = text
    start = 0
    while True:
        s = output.find("<think>", start)
        if s < 0:
            break
        e = output.find("</think>", s + 7)
        if e < 0:
            break
        reasoning_parts.append(output[s + 7 : e])
        output = output[:s] + output[e + 8 :]
        start = s
    return "\n".join(reasoning_parts), output


def estimate_prompt_tokens(messages: List[Dict[str, Any]]) -> Dict[str, int]:
    text_parts: List[str] = []
    image_tokens = 0
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    t = str(item.get("text") or "")
                    if t.strip():
                        text_parts.append(t)
        else:
            t = str(content or "")
            if t.strip():
                text_parts.append(t)
    text_tokens = _count_tokens("\n".join(text_parts))
    return {
        "text_tokens": text_tokens,
        "image_tokens": image_tokens,
        "prompt_tokens": text_tokens + image_tokens,
    }


def build_chat_usage(messages: List[Dict[str, Any]], completion_text: str) -> Dict[str, Any]:
    prompt = estimate_prompt_tokens(messages)
    reasoning_text, output_text = _split_think(completion_text or "")
    completion_text_tokens = _count_tokens(output_text)
    reasoning_tokens = _count_tokens(reasoning_text)
    output_tokens = completion_text_tokens + reasoning_tokens
    input_tokens = prompt["prompt_tokens"]
    total_tokens = input_tokens + output_tokens
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
        "prompt_tokens_details": {
            "cached_tokens": 0,
            "text_tokens": prompt["text_tokens"],
            "audio_tokens": 0,
            "image_tokens": prompt["image_tokens"],
        },
        "completion_tokens_details": {
            "text_tokens": completion_text_tokens,
            "audio_tokens": 0,
            "reasoning_tokens": reasoning_tokens,
        },
        "_raw": {
            "reasoning_tokens": reasoning_tokens,
            "cached_tokens": 0,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


def build_image_usage(prompt: str, success_count: int = 1) -> Dict[str, Any]:
    text_tokens = _count_tokens(str(prompt or ""))
    success = max(1, int(success_count or 1))
    total_tokens = text_tokens * success
    return {
        "total_tokens": total_tokens,
        "input_tokens": total_tokens,
        "output_tokens": 0,
        "input_tokens_details": {"text_tokens": text_tokens, "image_tokens": 0},
        "_raw": {
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "input_tokens": total_tokens,
            "output_tokens": 0,
        },
    }
