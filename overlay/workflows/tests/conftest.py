"""Shared pytest setup for overlay workflow tests.

The ``save_papers`` and ``research_brief`` workflows do
``from tools.semantic_scholar.client import SemanticScholarClient`` at
module import time. The real module lives under
``overlay/tools/semantic_scholar/`` and is reachable as
``tools.semantic_scholar.client`` because the API pod's ``app.py`` puts
the parent of each ``TOOL_DIRS`` entry (i.e. ``overlay/``) on
``sys.path``. The local test venv has neither the upstream ``tools``
namespace nor ``centaur_sdk`` installed.

Register placeholder packages + module in ``sys.modules`` exactly once
before any workflow module is imported by a test. Each test then patches
``<workflow>.SemanticScholarClient`` with a mock; the placeholder is
never called directly. Using explicit ``not in`` checks (rather than
``setdefault``) keeps the intent visible: if the real modules are ever
importable, we leave them alone.
"""

from __future__ import annotations

import sys
import types

if "tools" not in sys.modules:
    _tools_pkg = types.ModuleType("tools")
    _tools_pkg.__path__ = []  # type: ignore[attr-defined]
    sys.modules["tools"] = _tools_pkg

if "tools.semantic_scholar" not in sys.modules:
    _s2_pkg = types.ModuleType("tools.semantic_scholar")
    _s2_pkg.__path__ = []  # type: ignore[attr-defined]
    sys.modules["tools.semantic_scholar"] = _s2_pkg

if "tools.semantic_scholar.client" not in sys.modules:
    _client_stub = types.ModuleType("tools.semantic_scholar.client")
    _client_stub.SemanticScholarClient = object  # type: ignore[attr-defined]
    sys.modules["tools.semantic_scholar.client"] = _client_stub
