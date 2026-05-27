"""Unit tests for ideation seed-paper persistence helpers."""

from __future__ import annotations

from workflows.ideation import _child_workflow_output, _seed_paper_ids


def test_seed_paper_ids_extracts_in_order() -> None:
    papers = [
        {"paperId": "abc", "title": "A"},
        {"title": "no id"},
        {"paperId": "def", "title": "B"},
    ]
    assert _seed_paper_ids(papers) == ["abc", "def"]


def test_child_workflow_output_from_wait_response() -> None:
    result = {
        "status": "completed",
        "output_json": {
            "brief_document_id": "brief-1",
            "papers_inserted": 2,
        },
    }
    assert _child_workflow_output(result) == {
        "brief_document_id": "brief-1",
        "papers_inserted": 2,
    }


def test_child_workflow_output_empty_when_missing() -> None:
    assert _child_workflow_output(None) == {}
    assert _child_workflow_output({"status": "failed"}) == {}
