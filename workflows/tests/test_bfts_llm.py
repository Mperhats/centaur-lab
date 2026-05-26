"""Test: _bfts_llm wraps OpenAI/Anthropic LLM calls with function-call extraction."""
from __future__ import annotations

import json as json_module
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_llm import LLMCall, call_for_text, call_with_function, extract_code


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Patch ``asyncio.sleep`` inside ``_bfts_llm`` to be instant, but record
    the requested delays. Returned list lets tests assert the exact backoff
    schedule fired without spending real wall-clock seconds (1+2+4 = 7s per
    test, multiplied across the LLM suite, otherwise).

    Patches ``_bfts_llm.asyncio.sleep`` (not the top-level ``asyncio.sleep``)
    so other coroutines that happen to await sleep stay un-affected.
    """
    import asyncio

    import _bfts_llm

    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    # ``_bfts_llm`` does ``import asyncio`` (not ``from asyncio import
    # sleep``) so ``_bfts_llm.asyncio`` IS the asyncio module — patching
    # ``.sleep`` on it is module-global within this test, but
    # ``monkeypatch`` reverts at teardown so the next test sees the real
    # sleep again.
    monkeypatch.setattr(_bfts_llm.asyncio, "sleep", _fake_sleep)
    _ = asyncio  # imported above to make the patched binding's source explicit
    return sleeps


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


@pytest.mark.asyncio
async def test_anthropic_function_call_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_post(self, url, json=None, headers=None, **_):
        captured["url"] = url
        captured["headers"] = headers
        return httpx.Response(
            200,
            json={
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_x",
                    "name": "submit_review",
                    "input": {"is_bug": False, "summary": "ok"},
                }]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = await call_with_function(
        LLMCall(
            model="claude-sonnet-4-20250514",
            temperature=0.5,
            api_key="sk-ant-test",
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
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "sk-ant-test"


def test_extract_code_happy_path() -> None:
    plan, code = extract_code("some prefix text\n```python\nprint(1)\n```")
    assert plan == "some prefix text"
    assert code == "print(1)"


def test_extract_code_no_fence_returns_plan_only() -> None:
    plan, code = extract_code("no codeblock here, just words")
    assert plan == "no codeblock here, just words"
    assert code == ""


@pytest.mark.asyncio
async def test_call_with_function_raises_on_non_200(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
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
    # 500 is retryable so the F.5 backoff fires three times before the
    # standard "LLM call failed: 500" raises (4 attempts total).
    assert _no_sleep == [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# F.5: ``_post_with_retry`` exponential backoff on transient errors.
# ---------------------------------------------------------------------------


def _ok_response(url: str, content: str = "ok") -> httpx.Response:
    """200 OK with an OpenAI ``call_for_text``-shaped body."""
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", url),
    )


def _err_response(url: str, code: int, text: str = "transient") -> httpx.Response:
    return httpx.Response(code, text=text, request=httpx.Request("POST", url))


def _sequential_post(responses: list):
    """Build a fake ``httpx.AsyncClient.post`` that yields the next outcome
    from ``responses`` on each call. ``Exception`` entries are raised
    (network error simulation); ``httpx.Response`` entries are returned."""
    iterator = iter(responses)

    async def _post(self, url, json=None, headers=None, **_):
        outcome = next(iterator)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return _post


@pytest.mark.asyncio
async def test_post_with_retry_succeeds_after_one_429(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """A single 429 is followed by a 200; total: 2 attempts, one 1s sleep."""
    url = "https://api.openai.com/v1/chat/completions"
    monkeypatch.setattr(
        httpx.AsyncClient,
        "post",
        _sequential_post([_err_response(url, 429), _ok_response(url, "ok")]),
    )
    text = await call_for_text(
        LLMCall(model="gpt-4o", temperature=0.0, api_key="k", prompt="hi")
    )
    assert text == "ok"
    assert _no_sleep == [1.0]


@pytest.mark.asyncio
async def test_post_with_retry_does_not_retry_on_400(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """Non-retryable 4xx (other than 429) raises immediately, no sleep."""
    url = "https://api.openai.com/v1/chat/completions"
    monkeypatch.setattr(
        httpx.AsyncClient, "post", _sequential_post([_err_response(url, 400, "bad")])
    )
    with pytest.raises(RuntimeError, match="LLM call failed: 400"):
        await call_for_text(
            LLMCall(model="gpt-4o", temperature=0.0, api_key="k", prompt="hi")
        )
    assert _no_sleep == []


@pytest.mark.asyncio
async def test_post_with_retry_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """Four 503s in a row → RuntimeError, with 3 sleeps in 1s/2s/4s order."""
    url = "https://api.openai.com/v1/chat/completions"
    monkeypatch.setattr(
        httpx.AsyncClient,
        "post",
        _sequential_post([_err_response(url, 503) for _ in range(4)]),
    )
    with pytest.raises(RuntimeError, match="LLM call failed: 503"):
        await call_for_text(
            LLMCall(model="gpt-4o", temperature=0.0, api_key="k", prompt="hi")
        )
    assert _no_sleep == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_post_with_retry_handles_network_errors(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """``httpx.ConnectError`` is retryable; a 200 on the 3rd attempt unblocks."""
    url = "https://api.openai.com/v1/chat/completions"
    monkeypatch.setattr(
        httpx.AsyncClient,
        "post",
        _sequential_post(
            [
                httpx.ConnectError("conn refused"),
                httpx.ConnectError("conn refused"),
                _ok_response(url, "ok"),
            ]
        ),
    )
    text = await call_for_text(
        LLMCall(model="gpt-4o", temperature=0.0, api_key="k", prompt="hi")
    )
    assert text == "ok"
    assert _no_sleep == [1.0, 2.0]


@pytest.mark.asyncio
async def test_post_with_retry_raises_after_repeated_network_errors(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: list[float]
) -> None:
    """All four attempts raise ``RequestError`` → the wrapped RuntimeError
    surfaces a network-error message (distinct from the status-code path)."""
    monkeypatch.setattr(
        httpx.AsyncClient,
        "post",
        _sequential_post([httpx.ConnectError("nope") for _ in range(4)]),
    )
    with pytest.raises(RuntimeError, match="LLM call network error: nope"):
        await call_for_text(
            LLMCall(model="gpt-4o", temperature=0.0, api_key="k", prompt="hi")
        )
    assert _no_sleep == [1.0, 2.0, 4.0]
