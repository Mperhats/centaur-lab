"""Shared pytest bootstrap for the semantic_scholar tool tests.

The tool itself does ``from centaur_sdk import secret`` and is imported
as ``from semantic_scholar.client import SemanticScholarClient``.
Neither path is resolvable by default when ``pytest`` runs from this
directory because the local venv has no ``centaur_sdk`` installed and
the ``semantic_scholar`` directory is not a registered package.

This mirrors the ``sys.path`` bootstrap used by ``cli.py`` so tests can
import the client without depending on the API pod's editable installs.
"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
# The upstream centaur_sdk pyproject uses ``packages = ["."]`` which
# produces a flat-layout wheel that Python cannot import as
# ``centaur_sdk`` from an editable install. We prepend ``.centaur/`` so
# ``from centaur_sdk import secret`` resolves directly to the source.
_SDK_PARENT = _THIS_DIR.parents[3] / ".centaur"
if _SDK_PARENT.is_dir() and str(_SDK_PARENT) not in sys.path:
    sys.path.insert(0, str(_SDK_PARENT))
# Put ``overlay/tools/`` on sys.path so ``from semantic_scholar.client
# import SemanticScholarClient`` works the same way it does in the API
# pod (which prepends the parent of each ``TOOL_DIRS`` entry).
_TOOLS_DIR = _THIS_DIR.parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
# Put ``overlay/`` on sys.path so ``from centaur_lab.paper_document import ...``
# and ``from centaur_lab.metrics import ...`` resolve under tests. The API
# pod already has ``overlay/`` on sys.path via the tool loader; this mirrors
# that for local pytest runs. The package is named ``centaur_lab`` (not
# ``shared``) because upstream's tool loader reserves the ``shared.*``
# namespace for its tools runtime — see .centaur/services/api/api/tool_manager.py.
_OVERLAY_DIR = _THIS_DIR.parents[2]
if str(_OVERLAY_DIR) not in sys.path:
    sys.path.insert(0, str(_OVERLAY_DIR))
