"""VLM review of BFTS plots.

Reproduces Sakana's MinimalAgent._analyze_plots_with_vlm contract
(.scientist/ai_scientist/treesearch/parallel_agent.py:894-1033,
research 02 §VLM review). Encodes up to 10 plots as base64 image_url
content; calls a vision-capable model with vlm_feedback_spec; returns
{is_valid, per_plot_analyses, summary}.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

_MAX_PLOTS = 10
_VLM_MODEL = "gpt-4o-2024-11-20"
_VLM_TEMP = 0.5

_VLM_SPEC: dict = {
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


class VLMReviewer:
    def __init__(self, api_key: str, model: str = _VLM_MODEL) -> None:
        self.api_key = api_key
        self.model = model

    async def analyze_plots(
        self, *, plot_paths: list[str], task_desc: str
    ) -> dict:
        """Return {is_valid, per_plot_analyses, summary}.

        Caps to the first 10 plot_paths (Sakana uses an LLM judge to pick
        the best 10 when len > 10; for MVP we just truncate — known
        gap, tracked in Phase 4 deferred refinement).
        """
        keep = plot_paths[:_MAX_PLOTS]
        content: list[dict] = [
            {"type": "text", "text": f"Task: {task_desc}\nReview the plots; judge whether they are valid and informative."}
        ]
        for path in keep:
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            })

        body = {
            "model": self.model,
            "temperature": _VLM_TEMP,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": content}],
            "tools": [_VLM_SPEC],
            "tool_choice": {"type": "function", "function": {"name": "submit_vlm_feedback"}},
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"VLM call failed: {resp.status_code} {resp.text[:500]}")
        tool_call = resp.json()["choices"][0]["message"]["tool_calls"][0]
        args = json.loads(tool_call["function"]["arguments"])

        per_plot = [
            {"plot_index": idx, "analysis": entry.get("analysis", "")}
            for idx, entry in enumerate(args.get("plot_analyses") or [])
        ]
        return {
            "is_valid": bool(args.get("valid_plots_received")),
            "per_plot_analyses": per_plot,
            "summary": args.get("vlm_feedback_summary") or "",
        }


def _client() -> VLMReviewer:
    import os
    return VLMReviewer(api_key=os.getenv("OPENAI_API_KEY", ""))
