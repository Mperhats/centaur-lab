"""Guard the single-entrypoint contract for Slack-driven ``bfts_research``.

``bfts_runner.start_research`` runs in the API process, reads ``thread_key``
from the sandbox JWT, and calls ``create_workflow_run`` directly — bypassing
``POST /workflows/runs`` (which does not merge ``X-Centaur-Thread-Key`` into
the run input). The previous bash workaround
(``services/sandbox/call-workflow-run.sh``) was deleted as part of that
consolidation; this test prevents either the script or its skill-level
fallback from sneaking back in.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_call_workflow_run_shim_script_is_absent() -> None:
    shim = REPO_ROOT / "services" / "sandbox" / "call-workflow-run.sh"
    assert not shim.exists(), (
        f"{shim} re-introduced; use bfts_runner.start_research from sandbox "
        "instead — it runs in the API process and reads thread_key from the "
        "sandbox JWT, so client-side body injection is unnecessary."
    )


def test_bfts_experiments_skill_has_no_shim_fallback() -> None:
    skill = REPO_ROOT / ".agents" / "skills" / "bfts-experiments" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "call-workflow-run.sh" not in text, (
        "skill markdown references the deleted bash shim; remove the "
        "fallback block — bfts_runner.start_research is the only supported "
        "entrypoint for Slack-driven bfts_research runs."
    )
    assert "call bfts_runner start_research" in text, (
        "skill markdown must document `call bfts_runner start_research` as "
        "the canonical entrypoint."
    )
