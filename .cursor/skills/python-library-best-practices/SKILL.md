---
name: python-library-best-practices
description: Use when adding or restructuring an overlay tool, a workflow package, or a shared helper package in this repo. Aligned with upstream centaur conventions. Covers layout, pyproject.toml shape, public-API rules, module organization, and exception hierarchies.
---

# Python package best practices

For overlay tools (`overlay/tools/<tool>/`), workflows (`overlay/workflows/`), and shared helpers (`overlay/centaur_lab/`). Style rules — formatting, naming, dataclasses, errors — live in `python-style-guide`. This file covers structure: where things go, what gets exported, and how packages are organized.

Elegance is low lines of code, high reliability, and low maintenance burden. The rules below describe how upstream centaur packages are organised and are the target for new overlay packages.

## Simplicity first

**The core test.** Before adding any structural complexity — a subpackage, an `__all__`, a custom exception base, a `Protocol`, a new dependency — ask: *what does this let me do that the simpler version can't?* If you can't name a concrete thing, don't add it.

**Add the abstraction when it's forced, not before.** First case → one flat module, plain functions or a single client class, plain `ValueError` / `RuntimeError`. Second case → consider sharing. Genuine third case → abstract. The package base exception, the `_internal/` subpackage layout, and the explicit `__all__` below are *destinations* — reach for them when the package's surface earns them, not on day one.

Specific package-shaped patterns to refuse unless something concrete forces them:

- An `__all__` on a package nobody imports. It's a contract; don't write one with no counterparties.
- A custom `<Pkg>Error` base for a package with no callers outside its own tests, or with only one error type.
- A subpackage (`<pkg>/foo/__init__.py` + `_internal/`) when a single `foo.py` would do.
- A `Protocol` declared in a separate `types.py` when one consumer in one module needs it.
- A new tool package for a 20-line helper that belongs in `overlay/centaur_lab/`.
- A dependency added "in case we need it." Add it the second a real call site needs it.
- Splitting a module across files before any single file passes ~400 lines.

The package's structure should track its surface area. A one-file tool with one caller doesn't need the same scaffolding as a package the rest of the repo depends on.

## Stack

| Concern | Tool |
| --- | --- |
| Project manager | `uv` |
| Build backend | `hatchling` |
| Versioning | Hand-bumped `version = "0.1.0"` in `pyproject.toml` |
| Python | `>=3.11` |

`uv` for every dependency operation. Never call `pip` directly. We do **not** use `hatch-vcs`, `nox`, MkDocs, or PyPI publishing — these packages ship inside the API container, not as wheels.

## Layout

An overlay tool is a directory under `overlay/tools/` (or `.centaur/tools/<category>/<tool>/`) with its own `pyproject.toml`. Workflows live in `overlay/workflows/`. Shared helpers live in `overlay/centaur_lab/`.

```
overlay/tools/<tool>/
├── pyproject.toml
├── client.py              # module declared in [tool.centaur] module = ...
├── models.py
├── cli.py                 # optional — standalone CLI entrypoint
└── tests/
    ├── conftest.py
    └── test_*.py
```

No `tests/__init__.py` — pytest's `importlib` mode requires its absence. Keep modules flat. Promote to a subpackage only when a single file exceeds ~400 lines or needs its own private helpers; private modules are prefixed with `_`.

## `pyproject.toml`

Match what's in `overlay/tools/bfts_executor/pyproject.toml` and `.centaur/centaur_sdk/pyproject.toml`:

```toml
[project]
name = "<tool_or_workflow>"
description = "<one line>"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
package = false

[dependency-groups]
dev = ["pytest>=8.0.0", "pytest-asyncio>=0.23.0"]

[tool.pytest.ini_options]
asyncio_mode = "strict"          # optional; match the surrounding package

[tool.centaur]                   # tools only — declares the entry module
module = "client.py"
```

**Lower bounds only** (`>=`) on runtime deps. Never upper-cap (`<2`, `~=`, `==` are forbidden) — they cause unsolvable conflicts when the container layers multiple packages.

## Public API

A package with a real public surface — one other packages import, like `centaur_sdk` — ships a `py.typed` marker file and re-exports its API explicitly in `__init__.py`:

```python
"""Public API for <pkg>."""

from __future__ import annotations

from <pkg>._core import Client as Client
from <pkg>._core import create as create
from <pkg>.exceptions import PkgError as PkgError

__all__ = ["Client", "PkgError", "create"]
```

Rules for packages with a public surface:

1. Private modules start with `_` (`_core.py`, `_internal/`). Users importing them are off-warranty.
2. `__init__.py` is re-exports only — no computation, no side effects at import.
3. Use `from .x import Name as Name` so re-exports are explicit.
4. `__all__` is alphabetically sorted.
5. No wildcard imports anywhere.

A package with no external callers (a workflow consumed only by the workflow engine, a tool consumed only by the tool manager, an internal helper used by one sibling) does not need `__all__` or `py.typed`. The trigger for adding them is the second external caller.

## Module organization

A module that exists to *do one thing* — a client, a single workflow, a domain-specific helper — has whatever shape that thing needs. Two patterns cover most cases:

**Stateless module** — pure functions on plain dataclasses. Use when there's no per-call state to carry.

```python
# overlay/centaur_lab/brief.py
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class Brief:
    title: str
    summary: str

def from_paper(paper: Paper) -> Brief: ...
def render(brief: Brief) -> str: ...
```

**Client class** — a class that owns connection / auth / retry state, with verb-named methods. Use when the same handle is reused across calls (API client, DB pool wrapper, kube client).

```python
# overlay/tools/<tool>/client.py
from __future__ import annotations

import httpx

class Client:
    def __init__(self, *, api_key: str, base_url: str, max_retries: int = 3) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._http = httpx.AsyncClient()

    async def search(self, query: str) -> SearchResponse: ...
    async def fetch(self, id: str) -> Document: ...
    async def close(self) -> None:
        await self._http.aclose()
```

Pick whichever fits. Don't wrap a stateless transform in a class; don't scatter a stateful client across module-level functions sharing a global handle.

## Sync vs async

Pick one per package and stick to it.

- **Async** for anything that does I/O (HTTP, Postgres, Kubernetes, file streams on a remote FS). Use `httpx.AsyncClient` and `asyncpg`. This is the default for tool clients and workflows.
- **Sync** when the underlying library forces it (`psycopg` for one-shot DB scripts in `db/`) or when the operation is pure CPU.

Don't mix sync and async public functions on the same surface. CLIs (`*/cli.py`) call the async API through a one-line `asyncio.run(...)` wrapper.

## Errors

Plain `raise RuntimeError(...)` / `raise ValueError(...)` is the default. Only define a custom exception class when a caller will programmatically distinguish that failure mode from neighbours.

Three valid shapes, in order of decreasing simplicity — pick the smallest that fits:

1. **Stdlib exception.** Most cases. `raise RuntimeError("EXA_API_KEY not set.")`.
2. **One domain exception that inherits from a stdlib type.** When the failure has a name worth catching but there's only one of it:

   ```python
   class SlackAuthError(RuntimeError):
       """Raised when Slack rejects the bot token."""
   ```

3. **A package base with multiple subclasses.** When the package's public surface has several related failures callers will distinguish:

   ```python
   # exceptions.py
   class TwitterSDKError(Exception):
       """Base for all Twitter SDK errors."""

   class RateLimitError(TwitterSDKError):
       def __init__(self, msg: str, *, retry_after_s: float) -> None:
           super().__init__(msg)
           self.retry_after_s = retry_after_s

   class AuthenticationError(TwitterSDKError): ...
   class APIError(TwitterSDKError): ...
   ```

   Multi-inherit a stdlib type when callers might want either flavour: `ConfigError(PkgError, ValueError)`.

Don't define a `<Pkg>Error` base for a single subclass — use `RuntimeError` directly. Don't reach for typed exception fields until a caller actually needs them; a message string is enough until something programmatic depends on the structure.

Always chain at module boundaries: `raise X from Y`.

## Dependencies

Every dependency is a tax on the container build and on every consumer. Add one when the alternative is reimplementing something non-trivial; never for syntactic sugar. Prefer the libraries already in use across the repo (`httpx`, `asyncpg`, `pydantic`, `structlog`, `dataclasses-json`) over introducing a new package for the same job.
