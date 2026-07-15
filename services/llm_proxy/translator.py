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


# ═══════════════════════════════════════════════════════════════════════════════
# Reverse: Anthropic → OpenAI (for /v1/messages endpoint)
# ═══════════════════════════════════════════════════════════════════════════════

def anthropic_to_openai_request(an_body: dict) -> dict:
    """
    Convert Anthropic Messages request → OpenAI Chat Completions request.

    Anthropic:
      {model, system?, messages: [{role, content}], temperature, max_tokens, tools?}

    OpenAI:
      {model, messages: [{role, content, tool_calls?}], temperature, max_tokens, tools?}
    """
    model = an_body.get("model", "")
    temperature = an_body.get("temperature", 0.1)
    max_tokens = an_body.get("max_tokens", 4096)
    system = an_body.get("system")
    an_messages = an_body.get("messages", [])
    an_tools = an_body.get("tools")

    oa_messages = []

    # System prompt → first message
    if system:
        system_text = _extract_an_text(system)
        if system_text:
            oa_messages.append({"role": "system", "content": system_text})

    # Convert messages
    for msg in an_messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "user":
            text = _extract_an_text(content)
            if text:
                oa_messages.append({"role": "user", "content": text})

        elif role == "assistant":
            text = None
            tool_calls = []

            if isinstance(content, list):
                for block in content:
                    t = block.get("type", "")
                    if t == "text":
                        text = block.get("text", "")
                    elif t == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                            },
                        })
            elif isinstance(content, str):
                text = content

            msg_obj = {"role": "assistant"}
            if text:
                msg_obj["content"] = text
            if tool_calls:
                msg_obj["tool_calls"] = tool_calls
            oa_messages.append(msg_obj)

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": oa_messages,
    }
    if an_tools:
        body["tools"] = _an_tools_to_openai(an_tools)

    return body


def openai_to_anthropic_response(oa_data: dict, model: str) -> dict:
    """
    Convert OpenAI Chat Completions response → Anthropic Messages response.

    OpenAI:
      {id, object, created, model, choices: [{index, message: {role, content, tool_calls}, finish_reason}], usage}

    Anthropic:
      {id, type: "message", role: "assistant", content: [{type, text/...}], stop_reason, usage}
    """
    choices = oa_data.get("choices", [])
    usage = oa_data.get("usage", {})

    content_blocks = []

    if choices:
        choice = choices[0]
        message = choice.get("message", {})
        text = message.get("content")
        tool_calls = message.get("tool_calls")
        finish_reason = choice.get("finish_reason", "stop")

        if text:
            content_blocks.append({"type": "text", "text": text})

        if tool_calls:
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
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args,
                })

        # OpenAI finish_reason → Anthropic stop_reason
        reason_map = {
            "tool_calls": "tool_use",
            "stop": "end_turn",
            "length": "max_tokens",
        }
        stop_reason = reason_map.get(finish_reason, finish_reason)
    else:
        stop_reason = "end_turn"

    return {
        "id": oa_data.get("id", ""),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── Internal helpers (reverse direction) ────────────────────────────────────

def _extract_an_text(content) -> Optional[str]:
    """Extract text from Anthropic content (str, list of blocks, or single block)."""
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
    if isinstance(content, dict) and content.get("type") == "text":
        return content.get("text", "").strip() or None
    return str(content).strip() or None


def _an_tools_to_openai(an_tools: list) -> list:
    """Anthropic tools → OpenAI tools"""
    result = []
    for t in an_tools:
        result.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SSE Streaming converter: OpenAI → Anthropic
# ═══════════════════════════════════════════════════════════════════════════════

class OpenAISSEToAnthropic:
    """
    Accumulates OpenAI streaming chunks and emits Anthropic SSE events.

    Usage:
        conv = OpenAISSEToAnthropic(model="claude-sonnet-5")
        for line in sse_stream:
            event = conv.feed(line)
            if event:
                yield event
        # After stream ends:
        for event in conv.flush():
            yield event
    """

    def __init__(self, model: str = ""):
        self.model = model
        self.message_id = ""
        self.started = False
        self.content_block_index = 0
        self._text_buffer: list[str] = []
        self._finish_reason: str = ""

    def feed(self, raw_line: str) -> str:
        """
        Feed one raw SSE line (e.g. 'data: {...}').
        Returns an Anthropic SSE event string, or "" if nothing to emit yet.
        """
        line = raw_line.strip()
        if not line or line == "data: [DONE]":
            return ""

        if not line.startswith("data: "):
            return ""

        try:
            data = json.loads(line[6:])
        except json.JSONDecodeError:
            return ""

        choices = data.get("choices", [])
        if not choices:
            return ""

        choice = choices[0]
        delta = choice.get("delta", {})
        finish = choice.get("finish_reason") or ""

        # Capture message id from first chunk
        if not self.message_id:
            self.message_id = data.get("id", "")

        output = ""

        # On first text — emit message_start + content_block_start
        if not self.started:
            self.started = True
            content_block_start = {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
            output += self._make_message_start()
            output += _an_sse("content_block_start", content_block_start)

        # Accumulate and emit text deltas
        text = delta.get("content", "")
        if text is not None:
            text = text if isinstance(text, str) else str(text)
            if text:
                self._text_buffer.append(text)
                output += self._make_text_delta(text)

        # On finish
        if finish:
            self._finish_reason = finish
            output += self._make_content_block_stop()
            output += self._make_message_delta()
            output += self._make_message_stop()

        return output

    def flush(self) -> list[str]:
        """
        Call after stream ends. Returns any remaining events.
        """
        if not self.started:
            return []  # no content received at all

        if not self._finish_reason:
            # Stream ended without finish_reason — emit remaining events
            return [
                self._make_content_block_stop(),
                self._make_message_delta(),
                self._make_message_stop(),
            ]
        return []

    def _make_message_start(self) -> str:
        event = {
            "type": "message_start",
            "message": {
                "id": self.message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": self.model,
                "usage": {"input_tokens": 0},
            },
        }
        return _an_sse("message_start", event)

    def _make_text_delta(self, text: str) -> str:
        return _an_sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        })

    def _make_content_block_stop(self) -> str:
        return _an_sse("content_block_stop", {
            "type": "content_block_stop",
            "index": 0,
        })

    def _make_message_delta(self) -> str:
        reason_map = {
            "stop": "end_turn",
            "length": "max_tokens",
            "tool_calls": "tool_use",
        }
        return _an_sse("message_delta", {
            "type": "message_delta",
            "delta": {
                "stop_reason": reason_map.get(self._finish_reason, self._finish_reason),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": 0},
        })

    def _make_message_stop(self) -> str:
        return _an_sse("message_stop", {"type": "message_stop"})


def _an_sse(event_type: str, data: dict) -> str:
    """Format one Anthropic SSE event block."""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
