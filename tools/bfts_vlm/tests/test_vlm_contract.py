"""Tests for VLMReviewer.

Covers:
- ``analyze_plots`` contract shape (existing Phase 3 test).
- ``select_best_n_plots`` happy path + defensive fallbacks
  (Phase 4g.3 — Sakana's ``_analyze_plots_with_vlm`` picker port).
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tools.bfts_vlm.client import VLMReviewer


@pytest.mark.asyncio
async def test_analyze_returns_contract_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plot1 = tmp_path / "a.png"; plot1.write_bytes(b"\x89PNG_a")
    plot2 = tmp_path / "b.png"; plot2.write_bytes(b"\x89PNG_b")

    async def fake_post(self, url, json=None, headers=None, **_):
        return httpx.Response(
            200,
            json={
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_x",
                    "name": "submit_vlm_feedback",
                    "input": {
                        "plot_analyses": [
                            {"analysis": "looks fine"},
                            {"analysis": "also fine"},
                        ],
                        "valid_plots_received": True,
                        "vlm_feedback_summary": "plots are clean and informative",
                    },
                }]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    reviewer = VLMReviewer(api_key="sk-test")
    out = await reviewer.analyze_plots(
        plot_paths=[str(plot1), str(plot2)],
        task_desc="toy linreg MSE",
    )
    assert out["is_valid"] is True
    assert out["summary"] == "plots are clean and informative"
    assert len(out["per_plot_analyses"]) == 2
    assert out["per_plot_analyses"][0]["plot_index"] == 0
    assert out["per_plot_analyses"][0]["analysis"] == "looks fine"
    assert out["per_plot_analyses"][1]["plot_index"] == 1


def _make_anthropic_picker_response(filenames: list[str]) -> dict:
    """Build the Anthropic ``messages`` response shape for the picker tool."""
    return {
        "content": [{
            "type": "tool_use",
            "id": "toolu_pick",
            "name": "submit_plot_selection",
            "input": {"selected_filenames": filenames},
        }]
    }


def _make_paths(tmp_path: Path, names: list[str]) -> list[str]:
    paths: list[str] = []
    for name in names:
        p = tmp_path / name
        p.write_bytes(b"\x89PNG_" + name.encode())
        paths.append(str(p))
    return paths


@pytest.mark.asyncio
async def test_select_best_n_plots_happy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LLM returns three valid filenames -> three matching full paths, in LLM order."""
    paths = _make_paths(tmp_path, ["a.png", "b.png", "c.png", "d.png", "e.png"])

    async def fake_post(self, url, json=None, headers=None, **_):
        return httpx.Response(
            200,
            json=_make_anthropic_picker_response(["c.png", "a.png", "e.png"]),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    reviewer = VLMReviewer(api_key="sk-test")
    out = await reviewer.select_best_n_plots(
        plot_paths=paths, n=3, task_desc="ablation sweep",
    )
    assert out == [paths[2], paths[0], paths[4]]


@pytest.mark.asyncio
async def test_select_best_n_plots_filters_unknown_then_pads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unknown filenames are dropped; result is padded with input-order remainder to reach n."""
    paths = _make_paths(tmp_path, ["a.png", "b.png", "c.png", "d.png", "e.png"])

    async def fake_post(self, url, json=None, headers=None, **_):
        return httpx.Response(
            200,
            json=_make_anthropic_picker_response(["c.png", "ghost.png", "z.png"]),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    reviewer = VLMReviewer(api_key="sk-test")
    out = await reviewer.select_best_n_plots(
        plot_paths=paths, n=3, task_desc="ablation sweep",
    )
    # c.png kept, then pad with a.png, b.png (input order, skipping already-selected).
    assert out == [paths[2], paths[0], paths[1]]


@pytest.mark.asyncio
async def test_select_best_n_plots_pads_when_llm_returns_fewer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LLM picks fewer than n -> pad with first remaining paths in input order."""
    paths = _make_paths(tmp_path, ["a.png", "b.png", "c.png", "d.png"])

    async def fake_post(self, url, json=None, headers=None, **_):
        return httpx.Response(
            200,
            json=_make_anthropic_picker_response(["d.png"]),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    reviewer = VLMReviewer(api_key="sk-test")
    out = await reviewer.select_best_n_plots(
        plot_paths=paths, n=3, task_desc="ablation sweep",
    )
    assert out == [paths[3], paths[0], paths[1]]


@pytest.mark.asyncio
async def test_select_best_n_plots_silent_fallback_on_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Any LLM-call exception -> return ``plot_paths[:n]`` silently."""
    paths = _make_paths(tmp_path, ["a.png", "b.png", "c.png", "d.png", "e.png"])

    async def fake_post(self, url, json=None, headers=None, **_):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    reviewer = VLMReviewer(api_key="sk-test")
    out = await reviewer.select_best_n_plots(
        plot_paths=paths, n=3, task_desc="ablation sweep",
    )
    assert out == paths[:3]


@pytest.mark.asyncio
async def test_select_best_n_plots_noop_when_len_le_n(tmp_path: Path) -> None:
    """When ``len(plot_paths) <= n``, return them unchanged without calling the LLM.

    No ``httpx`` monkeypatch — the test will fail loudly if the picker
    issues a network call.
    """
    paths = _make_paths(tmp_path, ["a.png", "b.png", "c.png"])
    reviewer = VLMReviewer(api_key="sk-test")
    out = await reviewer.select_best_n_plots(
        plot_paths=paths, n=10, task_desc="ablation sweep",
    )
    assert out == paths


@pytest.mark.asyncio
async def test_select_best_n_plots_openai_routing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """OpenAI-shaped model strings hit the OpenAI chat-completions URL + tool_calls shape."""
    paths = _make_paths(tmp_path, ["a.png", "b.png", "c.png", "d.png", "e.png"])
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
                            "function": {
                                "name": "submit_plot_selection",
                                "arguments": '{"selected_filenames": ["b.png", "d.png"]}',
                            },
                        }],
                    },
                }]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    reviewer = VLMReviewer(api_key="sk-test")
    out = await reviewer.select_best_n_plots(
        plot_paths=paths, n=2, task_desc="ablation sweep", model="gpt-4o-2024-11-20",
    )
    assert out == [paths[1], paths[3]]
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
