---
name: python-library-best-practices
description: Use when adding or restructuring an overlay tool, a workflow package, or a shared helper package in this repo. Covers layout, pyproject.toml shape, public-API rules, module organization, and exception hierarchies. These are invariants — when existing code disagrees, the existing code is wrong and should be fixed as you touch it.
---

# Python package best practices

For overlay tools (`overlay/tools/<tool>/`), workflows (`overlay/workflows/`), and shared helpers (`overlay/centaur_lab/`). Style rules — formatting, naming, dataclasses, errors — live in `python-style-guide`. This file covers structure: where things go, what gets exported, and how packages are organized.

Elegance is low lines of code, high reliability, and low maintenance burden. The rules below produce strongly typed, maintainable packages by being narrow and prescriptive. They are invariants. When you encounter code that breaks them, fix it as you touch the file.

## Simplicity first

**The core test.** Before adding any structural complexity — a subpackage, an `__all__`, a custom exception base, a `Protocol`, an `aio.py` mirror, a new dependency — ask: *what does this let me do that the simpler version can't?* If you can't name a concrete thing, don't add it.

**Add the abstraction when it's forced, not before.** First case → one flat module, plain functions, plain `ValueError` / `RuntimeError`. Second case → consider sharing. Genuine third case → abstract. The four-symbol domain-module shape, the typed exception hierarchy, and the `_internal/` subpackage layout below are *destinations* — reach for them when the package's surface earns them, not on day one.

Specific package-shaped patterns to refuse unless something concrete forces them:

- An `__all__` on a package nobody imports. It's a contract; don't write a contract you have no counterparties for.
- A custom `<Pkg>Error` base for a package with no callers outside its own tests.
- An `aio.py` async mirror of a sync API that nobody calls async.
- A subpackage (`<pkg>/foo/__init__.py` + `_internal/`) when a single `foo.py` would do.
- A `Protocol` declared in a separate `types.py` when one consumer in one module needs it.
- A new tool package for a 20-line helper that belongs in `overlay/centaur_lab/`.
- A dependency added "in case we need it." Add it the second a real call site needs it.

The package's structure should track its surface area. A one-file tool with one caller doesn't need the same scaffolding as a package the rest of the repo depends on.

## Stack

| Concern | Tool |
| --- | --- |
| Project manager | `uv` |
| Build backend | `hatchling` |
| Versioning | Hand-bumped `version = "0.1.0"` in `pyproject.toml` |
| Python | `>=3.11` |

`uv` for every dependency operation. Never call `pip` directly. We do **not** use `hatch-vcs`, `nox`, MkDocs, or PyPI publishing — these packages ship inside the API container, not as wheels, so VCS-tagged versions and a docs pipeline would be unearned complexity.

## Layout

A package is a directory under `overlay/tools/` or `overlay/workflows/` with its own `pyproject.toml` and a `tests/` subfolder.

```
overlay/tools/<tool>/
├── pyproject.toml
├── client.py              # module declared in [tool.centaur] module = ...
├── models.py
└── tests/
    ├── conftest.py
    └── test_*.py
```

No `tests/__init__.py` — pytest's `importlib` mode requires its absence. Keep modules flat. Promote to a subpackage only when a single file exceeds ~400 lines or needs its own private helpers; private modules are prefixed with `_`.

## `pyproject.toml`

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
asyncio_mode = "strict"
filterwarnings = ["error"]

[tool.centaur]               # tools only — declares the entry module
module = "client.py"
```

**Lower bounds only** (`>=`) on runtime deps. Never upper-cap (`<2`, `~=`, `==` are forbidden) — they cause unsolvable conflicts when the container layers multiple packages.

## Public API

Every package with a public surface ships a `py.typed` marker file and re-exports its API explicitly in the top-level `__init__.py`:

```python
"""Public API for <pkg>."""
from <pkg>._core import Client as Client
from <pkg>._core import create as create
from <pkg>.exceptions import PkgError as PkgError
from <pkg>.exceptions import ConfigError as ConfigError

__all__ = ["Client", "ConfigError", "PkgError", "create"]
```

Rules:

1. Private modules start with `_` (`_core.py`, `_internal/`). Users importing them are off-warranty.
2. `__init__.py` is re-exports only — no computation, no side effects at import.
3. Use `from .x import Name as Name` so pyright strict accepts the re-export.
4. `__all__` is alphabetically sorted.
5. No wildcard imports anywhere.

A package with no public surface (purely internal helpers consumed by one other package in the same repo) does not need `__all__` or `py.typed`. The trigger for adding them is the second caller.

## Module organization — the four-symbol shape

A *domain* module — one users call directly — exports exactly four kinds of symbols, in this order:

1. **Types** — frozen dataclass(es), `Literal` aliases for discriminated unions, `Protocol`s for cross-module interfaces.
2. **Functions** — verb-only names operating on those types (`create`, `put`, `step`, `save`). The module already carries the noun; do not repeat it.
3. **Errors** — module-specific exception subclasses inheriting from the package base.
4. **Constants** — module-level `Final[T] = ...`. Nothing else runs at import time.

```python
# overlay/tools/<tool>/client.py
from dataclasses import dataclass
from typing import Final

from <tool>.exceptions import ToolError

# 1. Types
@dataclass(frozen=True)
class Config:
    timeout_s: float
    retries: int = 3

@dataclass(frozen=True)
class State:
    client: httpx.AsyncClient
    config: Config

# 2. Functions
async def create(config: Config) -> State: ...
async def request(state: State, *, url: str) -> Response: ...
async def close(state: State) -> None: ...

# 3. Errors
class RequestError(ToolError):
    """Raised when an upstream request fails after retries."""

# 4. Constants
DEFAULT_TIMEOUT_S: Final[float] = 30.0
```

Utility, infrastructure, and shared-type modules (`<pkg>.exceptions`, `<pkg>._internal.linalg`) are exempt — they exist to *support* domain modules, not to be domain modules themselves.

## Sync vs async

Pick one per package and stick to it. Don't mix sync and async public functions on the same surface.

- **Async** for anything that does I/O (HTTP, Postgres, Kubernetes, file streams on a remote FS). Use `httpx.AsyncClient` and `asyncpg`. This is the default for workflows and tool clients.
- **Sync** when the underlying library forces it (`psycopg` for one-shot DB scripts in `db/`, CPU-bound transforms with no I/O).

When a sync API genuinely needs an async wrapper, the sans-I/O core stays in one module and the async surface lives in `<pkg>/aio.py` — never mark the same function both ways.

## Errors

Every package with a public surface defines one base exception class and inherits from it:

```python
# <pkg>/exceptions.py
class PkgError(Exception):
    """Base for all <pkg> errors."""

class ConfigError(PkgError, ValueError):
    """Raised when user configuration is invalid."""

class RequestError(PkgError, OSError):
    """Raised when an upstream request fails."""
```

Multi-inherit from a stdlib class when semantically useful — callers can then catch either `PkgError` for the library-specific behavior or `OSError` for the generic category.

Exceptions raised across a module or package boundary carry **typed fields**, not just a message string:

```python
class ShapeError(PkgError, ValueError):
    def __init__(
        self,
        msg: str,
        *,
        expected: tuple[int, ...],
        actual: tuple[int, ...],
    ) -> None:
        super().__init__(msg)
        self.expected = expected
        self.actual = actual

# Raise
msg = f"shape {actual} != expected {expected}"
raise ShapeError(msg, expected=expected, actual=actual)

# Catch — typed access for programmatic handling
except ShapeError as exc:
    log.error("shape mismatch", extra={"expected": exc.expected, "actual": exc.actual})
```

The message is for humans; the fields are for callers. Inside one package, plain `raise ValueError(...)` for purely-internal failures no caller catches is fine — the custom hierarchy is required at the *public* surface.

## Dependencies

Every dependency is a tax on the container build and on every consumer. Add one when the alternative is reimplementing something non-trivial; never for syntactic sugar. Prefer the libraries already in use (`httpx`, `asyncpg`, `pydantic`, `dataclasses-json`) over introducing a new package for the same job.
