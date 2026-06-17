"""
LLM Proxy — OpenAI Chat Completions ↔ Anthropic Messages translator.

Pure functions — no I/O, no side effects.
"""

import json
import time
from typing import Optional


def openai_to_anthropic(oa_body: dict) -> dict:
    """
    Convert OpenAI Chat Completions request body → Anthropic Messages body.

    OpenAI:
      {model, messages: [{role, content, tool_calls?}], temperature, max_tokens, tools?}

    Anthropic:
      {model, system?, messages: [{role, content}], temperature, max_tokens, tools?}
    """
    model = oa_body.get("model", "")
    temperature = oa_body.get("temperature", 0.1)
    max_tokens = oa_body.get("max_tokens", 4096)
    oa_messages = oa_body.get("messages", [])
    oa_tools = oa_body.get("tools")

    system_prompt, an_messages = _oa_messages_to_anthropic(oa_messages)

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": an_messages,
    }
    if system_prompt:
        body["system"] = system_prompt
    if oa_tools:
        body["tools"] = _oa_tools_to_anthropic(oa_tools)

    return body


def anthropic_to_openai(an_data: dict, model: str) -> dict:
    """
    Convert Anthropic Messages response → OpenAI Chat Completions response.

    Anthropic:
      {id, content: [{type: "text"|"tool_use", ...}], stop_reason, usage: {input_tokens, output_tokens}}

    OpenAI:
      {id, object: "chat.completion", created, model,
       choices: [{index, message: {role, content?, tool_calls?}, finish_reason}],
       usage: {prompt_tokens, completion_tokens, total_tokens}}
    """
    content_blocks = an_data.get("content", [])
    stop_reason = an_data.get("stop_reason", "end_turn")
    usage = an_data.get("usage", {})

    text_parts = []
    tool_calls = []

    for block in content_blocks:
        t = block.get("type", "")
        if t == "text":
            text_parts.append(block.get("text", ""))
        elif t == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                },
            })

    text = "\n".join(text_parts) if text_parts else None

    # Anthropic stop_reason → OpenAI finish_reason
    reason_map = {
        "tool_use": "tool_calls",
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
    }
    finish_reason = reason_map.get(stop_reason, stop_reason)

    message = {"role": "assistant"}
    if text:
        message["content"] = text
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": an_data.get("id", ""),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


# ── Internal helpers ─────────────────────────────────────────────────────

def _oa_tools_to_anthropic(oa_tools: list) -> list:
    """OpenAI tools[{type, function: {name, description, parameters}}] → Anthropic tools[{name, description, input_schema}]"""
    result = []
    for t in oa_tools:
        fn = t.get("function", {})
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def _oa_messages_to_anthropic(oa_messages: list) -> tuple[Optional[str], list]:
    """OpenAI messages → (system_prompt, anthropic_messages)"""
    system_parts = []
    an_messages = []

    for msg in oa_messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "system":
            text = _extract_text(content)
            if text:
                system_parts.append(text)
            continue

        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            an_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                }],
            })
            continue

        if role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                blocks = []
                text = _extract_text(content)
                if text:
                    blocks.append({"type": "text", "text": text})
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    args_str = fn.get("arguments", "{}")
                    if isinstance(args_str, str):
                        try:
                            args = json.loads(args_str)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    else:
                        args = args_str
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args,
                    })
                an_messages.append({"role": "assistant", "content": blocks})
            else:
                text = _extract_text(content)
                if text:
                    an_messages.append({"role": "assistant", "content": text})
            continue

        # user + unknown roles → user
        text = _extract_text(content)
        if text:
            an_messages.append({"role": "user", "content": text})

    system = "\n\n".join(system_parts).strip() if system_parts else None
    return system, an_messages


def _extract_text(content) -> Optional[str]:
    """Extract text from content field (str, list of blocks, or None)."""
    if content is None:
        return None
    if isinstance(content, str):
        return content.strip() if content.strip() else None
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if t:
                    parts.append(t)
        return "\n".join(parts) if parts else None
    return str(content).strip() or None
