"""Structural invariants of the overlay repo.

These tests catch the kind of silent prod-vs-dev drift that's hard to
reproduce locally and only surfaces when an agent first calls a tool or
a workflow handler runs in production. Each test maps to a named
gotcha — keep failures pointed at the specific class of mistake.

If a test here fails, the fix is almost always a one-line edit to a
``pyproject.toml`` somewhere; the test message names the file.

Note: workspace-config consistency (root <-> tool pyprojects) is not
checked because the root pyproject discovers tools through a single
``[tool.uv.workspace].members = ["tools/*"]`` glob — there's nothing
per-tool to keep in sync. Newcomers run ``uv sync --all-packages``
(documented in README + scripts/ + CI) to aggregate every workspace
member's deps into the dev ``.venv``.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Python module name -> PyPI distribution name, for the (rare) cases
# where they diverge. Add an entry here when a new tool or workflow
# imports such a package.
#
# Examples (not currently in this overlay):
#   "yaml": "pyyaml"
#   "PIL":  "pillow"
#   "cv2":  "opencv-python"
_MODULE_TO_DIST: dict[str, str] = {}

# Names available on the API pod's ``sys.path`` without any pyproject
# dep declaration. Four kinds:
#
#   * Our own repo-root namespace packages: ``tools``, ``workflows``,
#     ``services`` (mounted at ``/app/overlay/org/`` via ``TOOL_DIRS`` /
#     ``WORKFLOW_DIRS``).
#   * ``packages``: the in-repo non-tool namespace (``packages/bfts_sdk``,
#     ``packages/centaur_sdk`` symlink). The API pod's ``app.py`` adds
#     ``Path(tool_dir).parent`` to ``sys.path`` for every ``TOOL_DIRS``
#     entry, which lands ``/app/overlay/org`` on ``sys.path``, so
#     ``from packages.bfts_sdk.X import …`` resolves alongside
#     ``from tools.<name>.X import …``. Same name-resolution mechanism
#     as ``tools``/``workflows``/``services``, so listed alongside them.
#   * ``centaur_sdk``: in dev via the ``packages/centaur_sdk`` symlink,
#     in the API pod via upstream's Dockerfile ``COPY centaur_sdk/
#     centaur_sdk/`` into ``/app/centaur_sdk/``.
#   * ``api``: upstream's own server package at ``/app/api/`` in the
#     API pod. NOT importable in dev/test, but the existing smoke
#     tests catch dev-only breakage there — if a workflow ever moves
#     ``from api.X import …`` out of an ``if TYPE_CHECKING:`` /
#     ``try: … except ImportError:`` guard, the smoke test fails on
#     ``import workflows.<name>``.
_API_POD_AVAILABLE_NAMES = frozenset(
    {"tools", "workflows", "services", "packages", "centaur_sdk", "api"}
)


def _normalize(name: str) -> str:
    """PEP 503-ish normalization so dist + module name comparisons stick."""
    return name.replace("_", "-").lower()


def _read_deps(pyproject: Path) -> set[str]:
    """Return the normalized distribution names from ``[project].dependencies``."""
    raw = tomllib.loads(pyproject.read_text()).get("project", {}).get("dependencies", [])
    out: set[str] = set()
    for spec in raw:
        head = spec.split(";", 1)[0].split("[", 1)[0]
        for op in (">=", "<=", "==", "~=", "!=", ">", "<"):
            head = head.split(op, 1)[0]
        head = head.strip()
        if head:
            out.add(_normalize(head))
    return out


def _tool_pyprojects() -> list[Path]:
    return sorted((REPO_ROOT / "tools").glob("*/pyproject.toml"))


def _all_tool_runtime_deps() -> set[str]:
    out: set[str] = set()
    for p in _tool_pyprojects():
        out.update(_read_deps(p))
    return out


def test_centaur_sdk_runtime_deps_satisfied() -> None:
    """``centaur_sdk`` is imported through a repo-root symlink, never
    pip-installed, so its own ``[project].dependencies`` are not pulled
    in transitively. Every SDK runtime dep must be carried by *some*
    tool's pyproject, or the API pod will hit ``ModuleNotFoundError``
    the first time a tool or workflow does ``import centaur_sdk``.

    When this test fails after a ``.centaur`` submodule bump, add the
    missing dep(s) to one of the per-tool pyprojects (typically the
    tool that's most likely to actually use the new SDK feature).
    """
    sdk_pp = REPO_ROOT / ".centaur" / "centaur_sdk" / "pyproject.toml"
    if not sdk_pp.exists():
        pytest.skip(".centaur submodule not initialized; skipping SDK dep check")

    sdk_deps = _read_deps(sdk_pp)
    tool_deps = _all_tool_runtime_deps()
    missing = sorted(sdk_deps - tool_deps)
    assert not missing, (
        f"centaur_sdk needs {missing} at runtime but no per-tool "
        f"pyproject.toml declares them. Add each to a "
        f"tools/<name>/pyproject.toml's [project].dependencies — "
        f"upstream's API entrypoint installs the union of all per-tool "
        f"deps, so adding to one tool is enough."
    )


class _RuntimeImportCollector(ast.NodeVisitor):
    """Collect module names from imports that aren't explicitly optional.

    Skips two idioms that mean "this import isn't required at runtime":

    * ``if TYPE_CHECKING:`` (and ``typing.TYPE_CHECKING``) — body
      never executes; the ``else:`` branch does, so we still walk it.
    * ``try: ... except ImportError: ...`` (and ``ModuleNotFoundError``,
      bare ``except:``, tuples thereof) — the ``try`` body's imports
      are explicitly optional. The ``except`` body's imports are still
      collected (they're the fallback that does need to resolve).

    Without these guards the test would flag every workflow that uses
    upstream's idiomatic ``try: from api.vm_metrics …`` pattern.
    """

    def __init__(self) -> None:
        self.modules: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.modules.add(alias.name.split(".", 1)[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Relative imports (``from .foo import …``) and ``from __future__``
        # are never third-party.
        if node.level == 0 and node.module and node.module != "__future__":
            self.modules.add(node.module.split(".", 1)[0])
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        if _has_import_error_handler(node.handlers):
            # ``try`` body imports are optional; skip. ``except`` /
            # ``finally`` / ``else`` bodies still walk normally — the
            # fallback they install must resolve at runtime.
            for child in node.handlers + list(node.orelse) + list(node.finalbody):
                self.visit(child)
            return
        self.generic_visit(node)


def _is_type_checking_guard(test: ast.expr) -> bool:
    """True for ``TYPE_CHECKING`` and ``typing.TYPE_CHECKING`` predicates."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _has_import_error_handler(handlers: list[ast.ExceptHandler]) -> bool:
    """True if any ``except`` clause catches ``ImportError`` (or a parent)."""
    targets = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}

    def _names(expr: ast.expr | None) -> set[str]:
        if expr is None:
            return {"BaseException"}  # bare ``except:``
        if isinstance(expr, ast.Name):
            return {expr.id}
        if isinstance(expr, ast.Tuple):
            return {n.id for n in expr.elts if isinstance(n, ast.Name)}
        return set()

    return any(targets & _names(h.type) for h in handlers)


# Object-creation statements in overlay migrations must be idempotent so
# that re-applying a file is a no-op when the object already exists. This
# is defence-in-depth against the schema-drift state described in
# ``docs/overlay-db-migrations.md`` ("Drift recovery") — without it, a
# manual ``DELETE FROM schema_migrations_overlay`` recovery step would
# fail with ``relation "X" already exists`` on objects that survived the
# original drop.
#
# The check is a regex against ``-- migrate:up`` blocks because we don't
# want to ship sqlglot just to validate three SQL files. Each pattern's
# anchor is the SQL keyword that introduces a new object.
_IDEMPOTENT_DDL_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"(?im)^\s*CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\b)",
        "CREATE TABLE … must be CREATE TABLE IF NOT EXISTS …",
    ),
    (
        r"(?im)^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?!IF\s+NOT\s+EXISTS\b)",
        "CREATE INDEX … must be CREATE INDEX IF NOT EXISTS …",
    ),
    (
        r"(?im)\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS\b)",
        "ADD COLUMN … must be ADD COLUMN IF NOT EXISTS …",
    ),
)


def _migrate_up_block(sql: str) -> str:
    """Return the ``-- migrate:up`` body of a dbmate migration file.

    dbmate splits each file into ``-- migrate:up`` and ``-- migrate:down``
    sections. We only require idempotency for the up direction; the down
    direction is allowed to use plain ``DROP`` because manual rollback
    via ``dbmate rollback`` always pairs the SQL with a stamp delete.
    """
    parts = re.split(
        r"^\s*--\s*migrate:down\s*$", sql, flags=re.IGNORECASE | re.MULTILINE
    )
    return parts[0]


def test_overlay_migrations_are_idempotent() -> None:
    """Every ``-- migrate:up`` DDL must use ``IF NOT EXISTS``.

    Catches the regression in `docs/overlay-db-migrations.md` ("Drift
    recovery"): a non-idempotent migration cannot be re-applied after a
    manual ``DELETE FROM schema_migrations_overlay`` step on the
    "objects survived but stamp was cleared" half of the drift state
    space, so the operator-facing recovery procedure breaks. Every new
    overlay migration this CI gate sees must keep the property — the
    failure message names the exact file + DDL keyword to fix.
    """
    migrations_dir = REPO_ROOT / "services" / "api" / "db" / "migrations"
    failures: list[str] = []
    for sql_path in sorted(migrations_dir.glob("*.sql")):
        up_block = _migrate_up_block(sql_path.read_text())
        for pattern, message in _IDEMPOTENT_DDL_PATTERNS:
            for match in re.finditer(pattern, up_block):
                line_no = up_block[: match.start()].count("\n") + 1
                failures.append(
                    f"{sql_path.relative_to(REPO_ROOT)}:{line_no} -- "
                    f"{message}"
                )
    assert not failures, (
        "Overlay migrations must use IF NOT EXISTS for all object "
        "creation in the migrate:up direction (see "
        "docs/overlay-db-migrations.md 'Drift recovery'):\n  "
        + "\n  ".join(failures)
    )


def test_workflow_imports_satisfiable_in_api_pod() -> None:
    """Every third-party import in ``workflows/`` must resolve from
    either the API base image (.centaur/services/api/pyproject.toml's
    ``[project].dependencies``) or some per-tool pyproject. The API
    pod's ``entrypoint.sh`` only ``uv pip install``s deps from
    ``TOOL_DIRS`` pyprojects; ``workflows/`` is not on that path.

    Workflow third-party deps therefore have to ride in via a tool's
    pyproject — not because the workflow uses the tool, but because
    upstream's entrypoint unions every tool's deps before launching
    uvicorn. This test enforces that contract mechanically so a
    workflow can't silently rely on something dev has but prod doesn't.
    """
    api_base = _read_deps(REPO_ROOT / ".centaur" / "services" / "api" / "pyproject.toml")
    tool_deps = _all_tool_runtime_deps()
    available = api_base | tool_deps
    stdlib = set(sys.stdlib_module_names)

    workflows_dir = REPO_ROOT / "workflows"
    failures: list[str] = []
    for wf in sorted(workflows_dir.glob("*.py")):
        if wf.name == "__init__.py":
            continue
        collector = _RuntimeImportCollector()
        collector.visit(ast.parse(wf.read_text()))

        for module in sorted(collector.modules):
            if module in stdlib or module in _API_POD_AVAILABLE_NAMES:
                continue
            dist = _normalize(_MODULE_TO_DIST.get(module, module))
            if dist in available:
                continue
            failures.append(
                f"{wf.relative_to(REPO_ROOT)} imports {module!r} at "
                f"runtime but no API base image dep or per-tool "
                f"pyproject declares the {dist!r} distribution. Add "
                f"{dist!r} to a tools/<name>/pyproject.toml, move the "
                f"import inside an ``if TYPE_CHECKING:`` guard if it's "
                f"only for type hints, or add a _MODULE_TO_DIST entry "
                f"in this test if the import name diverges from the "
                f"distribution name."
            )

    assert not failures, (
        "Workflow imports unsatisfiable in API pod:\n  " + "\n  ".join(failures)
    )
