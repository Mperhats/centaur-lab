"""Test: VLMReviewer.analyze_plots returns the contract shape."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import VLMReviewer


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
