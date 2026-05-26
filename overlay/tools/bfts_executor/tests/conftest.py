"""Shared pytest bootstrap for the bfts_executor tool tests.

The Centaur tool loader imports modules as ``shared.tools_runtime.<tool>``
(package context, see ``.centaur/services/api/api/tool_manager.py:1670-1707``),
which means ``client.py``'s peer imports must be relative
(``from .models import ...``). For pytest to load the tool the same way,
we put ``overlay/tools/`` on sys.path so the tests can use the
package-qualified import ``from bfts_executor.client import ...``.

Modeled on overlay/tools/semantic_scholar/tests/conftest.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_TOOLS_DIR = _THIS_DIR.parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
