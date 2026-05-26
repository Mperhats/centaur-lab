"""Integration: _bfts_state DAO against a real asyncpg pool.

Skips when CENTAUR_TEST_DATABASE_URL is unset (matches existing overlay
convention; see overlay/Justfile recipe test-workflows-integration).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _bfts_state import insert_node, insert_run, list_nodes_for_run, update_node_metric

pytestmark = pytest.mark.skipif(
    not os.getenv("CENTAUR_TEST_DATABASE_URL"),
    reason="set CENTAUR_TEST_DATABASE_URL to run (see db/README.md)",
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(os.environ["CENTAUR_TEST_DATABASE_URL"])
    yield p
    await p.close()


@pytest.mark.asyncio
async def test_insert_and_list(pool: asyncpg.Pool) -> None:
    run_id = f"test-{uuid.uuid4().hex}"
    await insert_run(
        pool,
        run_id=run_id,
        parent_run_id=None,
        idea={"name": "test"},
        config={"num_drafts": 1, "num_workers": 1, "max_debug_depth": 3, "debug_prob": 0.0},
        seed=0,
    )

    node_id = uuid.uuid4().hex
    await insert_node(
        pool,
        node_id=node_id,
        run_id=run_id,
        parent_node_id=None,
        step=0,
        stage_name="draft",
        plan="initial plan",
        code="print(1)",
    )

    metric = {
        "metric_names": [{
            "metric_name": "loss",
            "lower_is_better": True,
            "description": "",
            "data": [{"dataset_name": "d", "final_value": 0.5, "best_value": 0.5}],
        }]
    }
    await update_node_metric(
        pool,
        node_id=node_id,
        term_out=["hi\n"],
        exec_time_seconds=0.1,
        exc_type=None,
        exc_info=None,
        exc_stack=None,
        metric=metric,
        is_buggy=False,
        analysis="ran clean",
    )

    nodes = await list_nodes_for_run(pool, run_id=run_id)
    assert len(nodes) == 1
    n = nodes[0]
    assert n["node_id"] == node_id
    assert n["is_buggy"] is False
    assert json.loads(n["metric_json"])["metric_names"][0]["metric_name"] == "loss"
