"""Single OpenAI/Anthropic call helper.

Why this exists: every LLM call in the expansion pipeline is its own
ctx.step checkpoint. Routing all of them through one function keeps the
HTTP shape uniform and makes ctx.step's idempotency guarantees obvious
(research 02 §Agent turn shape lists all 5–7 calls; research 03
§Durability guarantees).

The provider is implied by the model string:
  - ``claude-*`` / ``anthropic.*`` → Anthropic Messages API (BFTS default).
  - ``gpt-*``                         → OpenAI chat/completions (legacy).

iron-proxy handles outbound: when this code runs inside the API pod the
``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` placeholder is substituted by
iron-proxy at the header layer (research 03 §Secrets / iron-proxy).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

_ANTHROPIC_VERSION = "2023-06-01"
_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


@dataclass
class LLMCall:
    model: str
    temperature: float
    api_key: str
    prompt: str
    max_tokens: int = 8192
    timeout: float = 120.0


def is_anthropic_model(model: str) -> bool:
    m = model.lower()
    return m.startswith("claude-") or m.startswith("anthropic.")


def _openai_tool_from_function_spec(function_spec: dict[str, Any]) -> dict[str, Any]:
    fn = function_spec["function"]
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
    }


def _anthropic_headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def _extract_anthropic_tool_input(
    data: dict[str, Any], *, tool_name: str
) -> dict[str, Any]:
    for block in data.get("content") or []:
        if block.get("type") != "tool_use":
            continue
        if block.get("name") != tool_name:
            continue
        inp = block.get("input")
        if isinstance(inp, dict):
            return inp
        if isinstance(inp, str):
            try:
                parsed = json.loads(inp)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"LLM returned malformed tool arguments: {e}; raw={inp[:500]}"
                ) from e
            if isinstance(parsed, dict):
                return parsed
        raise RuntimeError(f"LLM tool input was not an object: {inp!r}")
    raise RuntimeError("LLM did not invoke the tool")


def _extract_anthropic_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "".join(parts)


async def call_with_function(
    call: LLMCall, *, function_spec: dict[str, Any]
) -> dict[str, Any]:
    """Issue one LLM call forced to invoke ``function_spec``.

    Returns the *arguments* JSON the model passed to the function.
    Raises RuntimeError on any non-2xx or missing tool invocation.
    """
    if is_anthropic_model(call.model):
        return await _call_with_function_anthropic(call, function_spec=function_spec)
    return await _call_with_function_openai(call, function_spec=function_spec)


async def _call_with_function_openai(
    call: LLMCall, *, function_spec: dict[str, Any]
) -> dict[str, Any]:
    body = {
        "model": call.model,
        "temperature": call.temperature,
        "max_tokens": call.max_tokens,
        "messages": [{"role": "user", "content": call.prompt}],
        "tools": [function_spec],
        "tool_choice": {
            "type": "function",
            "function": {"name": function_spec["function"]["name"]},
        },
    }
    async with httpx.AsyncClient(timeout=call.timeout) as client:
        resp = await client.post(
            _OPENAI_CHAT_URL,
            json=body,
            headers={"Authorization": f"Bearer {call.api_key}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text[:500]}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned no choices")
    tool_calls = choices[0]["message"].get("tool_calls") or []
    if not tool_calls:
        raise RuntimeError("LLM did not invoke the tool")
    args_str = tool_calls[0]["function"]["arguments"]
    try:
        return json.loads(args_str)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"LLM returned malformed tool arguments: {e}; raw={args_str[:500]}"
        ) from e


async def _call_with_function_anthropic(
    call: LLMCall, *, function_spec: dict[str, Any]
) -> dict[str, Any]:
    tool = _openai_tool_from_function_spec(function_spec)
    body = {
        "model": call.model,
        "temperature": call.temperature,
        "max_tokens": call.max_tokens,
        "messages": [{"role": "user", "content": call.prompt}],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool["name"]},
    }
    async with httpx.AsyncClient(timeout=call.timeout) as client:
        resp = await client.post(
            _ANTHROPIC_MESSAGES_URL,
            json=body,
            headers=_anthropic_headers(call.api_key),
        )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text[:500]}")
    return _extract_anthropic_tool_input(resp.json(), tool_name=tool["name"])


async def call_for_text(call: LLMCall) -> str:
    """Issue one LLM call expecting a plain-text reply.

    Used by the draft/debug/improve prompts that ask for natural language
    followed by a single python codeblock — the caller extracts the
    codeblock with ``extract_code`` below.
    """
    if is_anthropic_model(call.model):
        return await _call_for_text_anthropic(call)
    return await _call_for_text_openai(call)


async def _call_for_text_openai(call: LLMCall) -> str:
    body = {
        "model": call.model,
        "temperature": call.temperature,
        "max_tokens": call.max_tokens,
        "messages": [{"role": "user", "content": call.prompt}],
    }
    async with httpx.AsyncClient(timeout=call.timeout) as client:
        resp = await client.post(
            _OPENAI_CHAT_URL,
            json=body,
            headers={"Authorization": f"Bearer {call.api_key}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text[:500]}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned no choices")
    return choices[0].get("message", {}).get("content") or ""


async def _call_for_text_anthropic(call: LLMCall) -> str:
    body = {
        "model": call.model,
        "temperature": call.temperature,
        "max_tokens": call.max_tokens,
        "messages": [{"role": "user", "content": call.prompt}],
    }
    async with httpx.AsyncClient(timeout=call.timeout) as client:
        resp = await client.post(
            _ANTHROPIC_MESSAGES_URL,
            json=body,
            headers=_anthropic_headers(call.api_key),
        )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text[:500]}")
    return _extract_anthropic_text(resp.json())


def extract_code(text: str) -> tuple[str, str]:
    """Extract (plan, python_code) from natural-language + codeblock reply.

    Mirrors Sakana's response.extract_text_up_to_code +
    response.extract_code (utils/response.py:55-83).
    """
    fence = "```python"
    idx = text.find(fence)
    if idx == -1:
        idx = text.find("```")
        if idx == -1:
            return text.strip(), ""
        fence = "```"
    plan = text[:idx].rstrip()
    rest = text[idx + len(fence):]
    end = rest.find("```")
    if end == -1:
        return plan, rest.strip()
    return plan, rest[:end].strip()
