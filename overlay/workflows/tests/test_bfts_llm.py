"""Test: _bfts_llm wraps OpenAI chat-completions with function-call extraction."""
from __future__ import annotations

import json as json_module
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_llm import LLMCall, call_with_function, extract_code


@pytest.mark.asyncio
async def test_function_call_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_post(self, url, json=None, headers=None, **_):
        captured["url"] = url
        captured["body"] = json
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "id": "x",
                            "type": "function",
                            "function": {
                                "name": "submit_review",
                                "arguments": json_module.dumps({"is_bug": False, "summary": "ok"}),
                            },
                        }]
                    }
                }]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = await call_with_function(
        LLMCall(
            model="gpt-4o-2024-11-20",
            temperature=0.5,
            api_key="sk-test",
            prompt="judge",
        ),
        function_spec={
            "type": "function",
            "function": {
                "name": "submit_review",
                "description": "judge",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "is_bug": {"type": "boolean"},
                        "summary": {"type": "string"},
                    },
                    "required": ["is_bug", "summary"],
                },
            },
        },
    )
    assert out == {"is_bug": False, "summary": "ok"}
    assert captured["url"].endswith("/v1/chat/completions")


def test_extract_code_happy_path() -> None:
    plan, code = extract_code("some prefix text\n```python\nprint(1)\n```")
    assert plan == "some prefix text"
    assert code == "print(1)"


def test_extract_code_no_fence_returns_plan_only() -> None:
    plan, code = extract_code("no codeblock here, just words")
    assert plan == "no codeblock here, just words"
    assert code == ""


@pytest.mark.asyncio
async def test_call_with_function_raises_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(self, url, json=None, headers=None, **_):
        return httpx.Response(500, text="upstream broken", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    call = LLMCall(model="gpt-4o", temperature=0.5, api_key="x", prompt="hi")
    with pytest.raises(RuntimeError, match="LLM call failed: 500"):
        await call_with_function(
            call,
            function_spec={
                "type": "function",
                "function": {
                    "name": "n",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
        )
