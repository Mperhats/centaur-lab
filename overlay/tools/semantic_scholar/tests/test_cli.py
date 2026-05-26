"""Tests for the standalone ``semantic_scholar`` CLI entrypoint.

Only the ``research-brief`` subcommand is covered here — the existing
``search``/``paper``/``references`` commands ride on the same factory
and have been exercised by hand against the live S2 API. The point of
this file is to pin the output-shape contract for the new
``research-brief`` command so a refactor of the underlying tool method
can't silently break the operator-facing CLI surface (markdown-only
under ``--pretty``, JSON under ``--json``, Rich summary by default,
exit 1 with a red error line on the ``error`` envelope, and a clear
parse error when both flags are passed at once).

All tests patch ``SemanticScholarClient.research_brief`` at the class
level so the ``_make_client()`` factory inside ``cli.py`` returns a
real instance whose ``research_brief`` is the configured stub. No
network, DB, or asyncio loop is involved.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from semantic_scholar.cli import app
from semantic_scholar.client import SemanticScholarClient


def _success_result(*, markdown: str = "# Brief\n\nBody paragraph.") -> dict[str, Any]:
    """Canonical success envelope for ``research_brief``.

    Mirrors the shape documented on ``SemanticScholarClient.research_brief``:
    ``status="completed"`` plus the brief id, brief action tag, the paper
    upsert counters, and the rendered markdown the caller posts back to
    the user.
    """
    return {
        "status": "completed",
        "brief_document_id": "semantic_scholar:research_brief:abc123",
        "brief_action": "inserted",
        "results_count": 3,
        "papers_inserted": 2,
        "papers_updated": 1,
        "papers_noop": 0,
        "markdown": markdown,
    }


def _install_research_brief(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]
) -> list[dict[str, Any]]:
    """Patch the tool method at the class level and record each call.

    Returning the call log lets a test assert on the args the CLI
    forwarded — particularly useful for the default-summary case where
    the rendered output doesn't echo back the limit/year_from flags.
    """
    calls: list[dict[str, Any]] = []

    def _stub(
        self: SemanticScholarClient,
        query: str,
        limit: int = 5,
        year_from: int | None = None,
    ) -> dict[str, Any]:
        calls.append({"query": query, "limit": limit, "year_from": year_from})
        return result

    monkeypatch.setattr(
        SemanticScholarClient, "research_brief", _stub, raising=True
    )
    return calls


def test_research_brief_cmd_pretty_prints_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _success_result(markdown="# Brief: graph rag\n\nFirst line.\nSecond line.")
    _install_research_brief(monkeypatch, expected)

    runner = CliRunner()
    result = runner.invoke(app, ["research-brief", "graph rag", "--pretty"])

    assert result.exit_code == 0, result.output
    # ``print(...)`` adds exactly one trailing newline; the markdown
    # itself should otherwise be byte-identical to ``result["markdown"]``.
    assert result.stdout == expected["markdown"] + "\n"


def test_research_brief_cmd_json_prints_full_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _success_result()
    _install_research_brief(monkeypatch, expected)

    runner = CliRunner()
    result = runner.invoke(app, ["research-brief", "graph rag", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == expected


def test_research_brief_cmd_default_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _success_result()
    calls = _install_research_brief(monkeypatch, expected)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["research-brief", "graph rag", "--limit", "7", "--year-from", "2020"],
    )

    assert result.exit_code == 0, result.output
    output = result.stdout
    # Brief id, action, and the upsert counters must be visible in the
    # default Rich summary so operators can sanity-check persistence at
    # a glance without piping through ``--json``.
    assert expected["brief_document_id"] in output
    assert expected["brief_action"] in output
    assert str(expected["results_count"]) in output
    assert str(expected["papers_inserted"]) in output
    assert str(expected["papers_updated"]) in output

    # Pin that the CLI forwarded the flags to the tool method rather
    # than silently dropping them — the recipe-level interface relies
    # on these being plumbed through.
    assert calls == [{"query": "graph rag", "limit": 7, "year_from": 2020}]


def test_research_brief_cmd_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error_result = {
        "status": "error",
        "error": "DATABASE_URL is required for semantic_scholar.research_brief",
    }
    _install_research_brief(monkeypatch, error_result)

    runner = CliRunner()
    result = runner.invoke(app, ["research-brief", "graph rag"])

    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output


def test_research_brief_cmd_mutually_exclusive_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Patch the tool method anyway so even if validation accidentally
    # falls through, the network/DB path can't be reached.
    _install_research_brief(monkeypatch, _success_result())

    runner = CliRunner()
    result = runner.invoke(
        app, ["research-brief", "graph rag", "--pretty", "--json"]
    )

    assert result.exit_code != 0
    # The error message must name both flags so the operator can fix
    # the invocation without rereading ``--help``.
    assert "--pretty" in result.output
    assert "--json" in result.output
