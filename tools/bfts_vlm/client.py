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

# Picker function-call schema (Phase 4g.3 — Sakana's plot_selection_spec port,
# `.scientist/ai_scientist/treesearch/parallel_agent.py:205-220`). The picker
# is a text-only feedback call: it receives plot filenames + the task
# description and returns the N most informative filenames for VLM review.
_PICKER_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_plot_selection",
        "description": "Return the N most informative plot filenames for VLM review.",
        "parameters": {
            "type": "object",
            "properties": {
                "selected_filenames": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Plot filenames in order of importance.",
                }
            },
            "required": ["selected_filenames"],
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


def _anthropic_tool_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    fn = spec["function"]
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
    }


def _anthropic_tool() -> dict[str, Any]:
    return _anthropic_tool_from_spec(_VLM_SPEC)


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


def _resolve_picked_paths(
    selected_filenames: list[str], plot_paths: list[str], n: int
) -> list[str]:
    """Map LLM-returned basenames back to full paths, then pad to ``n``.

    LLM output is best-effort: it may name files that aren't in
    ``plot_paths`` (hallucination), repeat names, or return fewer than
    ``n``. Caller always gets exactly ``min(n, len(plot_paths))`` paths
    so the downstream VLM call has a stable batch size.
    """
    by_basename: dict[str, str] = {}
    for path in plot_paths:
        by_basename.setdefault(Path(path).name, path)

    resolved: list[str] = []
    seen: set[str] = set()
    for name in selected_filenames:
        if not isinstance(name, str):
            continue
        path = by_basename.get(Path(name).name)
        if path is None or path in seen:
            continue
        resolved.append(path)
        seen.add(path)
        if len(resolved) == n:
            return resolved

    for path in plot_paths:
        if len(resolved) == n:
            break
        if path not in seen:
            resolved.append(path)
            seen.add(path)
    return resolved


def _picker_prompt(plot_paths: list[str], n: int, task_desc: str) -> str:
    filenames = "\n".join(f"- {Path(p).name}" for p in plot_paths)
    return (
        "You are an experienced AI researcher selecting plots for VLM review.\n"
        f"Task: {task_desc}\n\n"
        f"{len(plot_paths)} plots were produced; pick the {n} most informative "
        "for judging the experiment.\n"
        "For similar plots (e.g. generated samples at each epoch) select at most "
        "5 at a suitable interval.\n\n"
        "Candidate filenames:\n"
        f"{filenames}\n\n"
        "Return exactly the chosen filenames via submit_plot_selection."
    )


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

        Caps to the first ``_MAX_PLOTS`` plot_paths. Callers that may
        produce more plots than the VLM batch limit should pre-filter
        with :meth:`select_best_n_plots`; the truncation here is a
        defense-in-depth fallback so this method never overshoots.
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

    async def select_best_n_plots(
        self, *, plot_paths: list[str], n: int, task_desc: str, model: str | None = None
    ) -> list[str]:
        """Pick the ``n`` most informative plots for VLM review.

        Sakana's ``_analyze_plots_with_vlm`` (`parallel_agent.py:910-980`)
        calls a feedback model with ``plot_selection_spec`` when there are
        more than ``_MAX_PLOTS`` plots; this method ports that selection
        step as a text-only function-call into the same routing the VLM
        review uses. Returns full paths (not basenames) in LLM-preferred
        order, padded with input-order remainder when the LLM under-picks
        or names files that don't exist.

        Fast path: when ``len(plot_paths) <= n`` no LLM call is issued —
        the caller already has fewer plots than the requested batch.
        On any exception during the LLM call the method silently
        returns ``plot_paths[:n]`` — Sakana's same fallback, and the
        worst case is the legacy truncation behaviour.
        """
        if len(plot_paths) <= n:
            return list(plot_paths)
        use_model = model or self.model
        try:
            if _is_anthropic_model(use_model):
                selected = await self._select_best_n_plots_anthropic(
                    plot_paths=plot_paths, n=n, task_desc=task_desc, model=use_model
                )
            else:
                selected = await self._select_best_n_plots_openai(
                    plot_paths=plot_paths, n=n, task_desc=task_desc, model=use_model
                )
        except Exception:
            return plot_paths[:n]
        return _resolve_picked_paths(selected, plot_paths, n)

    async def _select_best_n_plots_openai(
        self, *, plot_paths: list[str], n: int, task_desc: str, model: str
    ) -> list[str]:
        body = {
            "model": model,
            "temperature": _VLM_TEMP,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": _picker_prompt(plot_paths, n, task_desc)}],
            "tools": [_PICKER_SPEC],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_plot_selection"},
            },
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                _OPENAI_CHAT_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key(model)}"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"plot picker failed: {resp.status_code} {resp.text[:500]}")
        tool_call = resp.json()["choices"][0]["message"]["tool_calls"][0]
        args = json.loads(tool_call["function"]["arguments"])
        return list(args.get("selected_filenames") or [])

    async def _select_best_n_plots_anthropic(
        self, *, plot_paths: list[str], n: int, task_desc: str, model: str
    ) -> list[str]:
        tool = _anthropic_tool_from_spec(_PICKER_SPEC)
        body = {
            "model": model,
            "temperature": _VLM_TEMP,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": _picker_prompt(plot_paths, n, task_desc)}],
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
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
            raise RuntimeError(f"plot picker failed: {resp.status_code} {resp.text[:500]}")
        data = resp.json()
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == tool["name"]:
                inp = block.get("input")
                if isinstance(inp, dict):
                    return list(inp.get("selected_filenames") or [])
                if isinstance(inp, str):
                    parsed = json.loads(inp)
                    return list(parsed.get("selected_filenames") or [])
        raise RuntimeError("plot picker did not invoke submit_plot_selection")

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
