"""VLM review of BFTS plots.

Reproduces Sakana's MinimalAgent._analyze_plots_with_vlm contract
(.scientist/ai_scientist/treesearch/parallel_agent.py:894-1033,
research 02 §VLM review). Encodes up to 10 plots as base64 image content;
calls a vision-capable model with vlm_feedback_spec; returns
{is_valid, per_plot_analyses, summary}.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx

_MAX_PLOTS = 10
_VLM_MODEL = "claude-sonnet-4-20250514"
_VLM_TEMP = 0.5
_ANTHROPIC_VERSION = "2023-06-01"
_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"

_VLM_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_vlm_feedback",
        "description": "Review the plots and judge their validity.",
        "parameters": {
            "type": "object",
            "properties": {
                "plot_analyses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"analysis": {"type": "string"}},
                        "required": ["analysis"],
                    },
                },
                "valid_plots_received": {"type": "boolean"},
                "vlm_feedback_summary": {"type": "string"},
            },
            "required": ["plot_analyses", "valid_plots_received", "vlm_feedback_summary"],
        },
    },
}


def _is_anthropic_model(model: str) -> bool:
    m = model.lower()
    return m.startswith("claude-") or m.startswith("anthropic.")


def _api_key_for_model(model: str) -> str:
    """Iron-proxy placeholder for the model's provider (matches semantic_scholar)."""
    from centaur_sdk import secret

    if _is_anthropic_model(model):
        return secret("ANTHROPIC_API_KEY", "")
    return secret("OPENAI_API_KEY", "")


def _anthropic_tool() -> dict[str, Any]:
    fn = _VLM_SPEC["function"]
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
    }


def _parse_vlm_args(args: dict[str, Any]) -> dict[str, Any]:
    per_plot = [
        {"plot_index": idx, "analysis": entry.get("analysis", "")}
        for idx, entry in enumerate(args.get("plot_analyses") or [])
    ]
    return {
        "is_valid": bool(args.get("valid_plots_received")),
        "per_plot_analyses": per_plot,
        "summary": args.get("vlm_feedback_summary") or "",
    }


class VLMReviewer:
    def __init__(self, api_key: str | None = None, model: str = _VLM_MODEL) -> None:
        # Explicit api_key is for unit tests; production resolves via secret().
        self._api_key_override = api_key
        self.model = model

    def _api_key(self, model: str) -> str:
        if self._api_key_override is not None:
            return self._api_key_override
        return _api_key_for_model(model)

    async def analyze_plots(
        self, *, plot_paths: list[str], task_desc: str, model: str | None = None
    ) -> dict:
        """Return {is_valid, per_plot_analyses, summary}.

        Caps to the first 10 plot_paths (Sakana uses an LLM judge to pick
        the best 10 when len > 10; for MVP we just truncate — known
        gap, tracked in Phase 4 deferred refinement).
        """
        use_model = model or self.model
        keep = plot_paths[:_MAX_PLOTS]
        if _is_anthropic_model(use_model):
            return await self._analyze_plots_anthropic(
                plot_paths=keep, task_desc=task_desc, model=use_model
            )
        return await self._analyze_plots_openai(
            plot_paths=keep, task_desc=task_desc, model=use_model
        )

    async def _analyze_plots_openai(
        self, *, plot_paths: list[str], task_desc: str, model: str
    ) -> dict:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Task: {task_desc}\n"
                    "Review the plots; judge whether they are valid and informative."
                ),
            }
        ]
        for path in plot_paths:
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            })

        body = {
            "model": model,
            "temperature": _VLM_TEMP,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": content}],
            "tools": [_VLM_SPEC],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_vlm_feedback"},
            },
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                _OPENAI_CHAT_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key(model)}"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"VLM call failed: {resp.status_code} {resp.text[:500]}")
        tool_call = resp.json()["choices"][0]["message"]["tool_calls"][0]
        args = json.loads(tool_call["function"]["arguments"])
        return _parse_vlm_args(args)

    async def _analyze_plots_anthropic(
        self, *, plot_paths: list[str], task_desc: str, model: str
    ) -> dict:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Task: {task_desc}\n"
                    "Review the plots; judge whether they are valid and informative."
                ),
            }
        ]
        for path in plot_paths:
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": encoded,
                },
            })

        tool = _anthropic_tool()
        body = {
            "model": model,
            "temperature": _VLM_TEMP,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": content}],
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                _ANTHROPIC_MESSAGES_URL,
                json=body,
                headers={
                    "x-api-key": self._api_key(model),
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(f"VLM call failed: {resp.status_code} {resp.text[:500]}")
        data = resp.json()
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == tool["name"]:
                inp = block.get("input")
                if isinstance(inp, dict):
                    return _parse_vlm_args(inp)
                if isinstance(inp, str):
                    return _parse_vlm_args(json.loads(inp))
        raise RuntimeError("VLM did not invoke submit_vlm_feedback")


def _client() -> VLMReviewer:
    return VLMReviewer()
