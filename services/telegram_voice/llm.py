import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://aichat-okil-sato.kartochka.tech/api/v1/chat/completions")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4.5")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))


async def ask(prompt: str, system_prompt: str = "", tools: list[dict] | None = None) -> str:
    """Отправить вопрос LLM и получить ответ."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_TOKENS,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(verify=False, timeout=60) as client:
        r = await client.post(LLM_BASE_URL, json=body, headers=headers)
        r.raise_for_status()
        data = r.json()

    choice = data["choices"][0]
    msg = choice["message"]

    # Если LLM хочет вызвать инструмент — логируем, но пока возвращаем текстовый ответ
    tool_calls = msg.get("tool_calls", [])
    if tool_calls:
        logger.info(f"LLM requested tool calls: {json.dumps(tool_calls, ensure_ascii=False)}")

    content = msg.get("content", "")
    if not content and tool_calls:
        # LLM только вызвала инструменты — формируем ответ из tool_calls
        tc_names = [tc["function"]["name"] for tc in tool_calls]
        content = f"Запрос инструментов: {', '.join(tc_names)}"

    return content.strip()


async def ask_with_tools(
    prompt: str,
    system_prompt: str = "",
    tools: list[dict] | None = None,
    tool_handler=None,
    max_rounds: int = 3,
) -> str:
    """LLM с циклом function calling: может вызывать инструменты до max_rounds раз."""
    if not tool_handler:
        return await ask(prompt, system_prompt, tools)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    for _ in range(max_rounds):
        body = {
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": LLM_TEMPERATURE,
            "max_tokens": LLM_MAX_TOKENS,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(verify=False, timeout=60) as client:
            r = await client.post(LLM_BASE_URL, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()

        msg = data["choices"][0]["message"]
        tool_calls = msg.get("tool_calls", [])

        if tool_calls:
            messages.append(msg)  # assistant message with tool_calls
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"])
                logger.info(f"LLM → tool call: {fn_name}({json.dumps(fn_args, ensure_ascii=False)})")
                try:
                    result = await tool_handler(fn_name, fn_args)
                except Exception as e:
                    result = f"ERROR: {e}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })
        else:
            return msg.get("content", "").strip()

    # Исчерпали раунды — последний ответ
    return messages[-1].get("content", "").strip()
