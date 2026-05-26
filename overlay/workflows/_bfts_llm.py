"""Single OpenAI/Anthropic call helper.

Why this exists: every LLM call in the expansion pipeline is its own
ctx.step checkpoint. Routing all of them through one function keeps the
HTTP shape uniform and makes ctx.step's idempotency guarantees obvious
(research 02 §Agent turn shape lists all 5–7 calls; research 03
§Durability guarantees).

The provider is implied by the model string:
  - ``gpt-*``      → OpenAI (the only path Phase 2 exercises; Sakana's
    defaults for both agent.code and agent.feedback are gpt-4o, research
    02 §(c) Model / provider params).
  - ``anthropic.*`` / ``claude-*`` → Anthropic. Deferred to Phase 4g (the
    multi-provider switch is one extra branch in :func:`call_for_text`).

iron-proxy handles outbound: when this code runs inside the API pod the
``OPENAI_API_KEY`` placeholder is substituted by iron-proxy at the
header layer (research 03 §Secrets / iron-proxy).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class LLMCall:
    model: str
    temperature: float
    api_key: str
    prompt: str
    max_tokens: int = 8192


async def call_with_function(
    call: LLMCall, *, function_spec: dict[str, Any]
) -> dict[str, Any]:
    """Issue one chat-completions call forced to invoke ``function_spec``.

    Returns the *arguments* JSON the model passed to the function.
    Raises RuntimeError on any non-2xx or missing tool_calls.
    """
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
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
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
    return json.loads(args_str)


async def call_for_text(call: LLMCall) -> str:
    """Issue one chat-completions call expecting a plain-text reply.

    Used by the draft/debug/improve prompts that ask for natural language
    followed by a single python codeblock — the caller extracts the
    codeblock with ``extract_code`` below.
    """
    body = {
        "model": call.model,
        "temperature": call.temperature,
        "max_tokens": call.max_tokens,
        "messages": [{"role": "user", "content": call.prompt}],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {call.api_key}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text[:500]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"] or ""


def extract_code(text: str) -> tuple[str, str]:
    """Extract (plan, python_code) from natural-language + codeblock reply.

    Mirrors Sakana's response.extract_text_up_to_code +
    response.extract_code (utils/response.py:55-83).
    """
    fence = "```python"
    idx = text.find(fence)
    if idx == -1:
        # Fall back to plain ``` fence.
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
