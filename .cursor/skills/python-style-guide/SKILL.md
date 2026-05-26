---
name: python-style-guide
description: Use when writing, editing, or reviewing any Python in this repo (overlay tools, workflows, centaur_lab helpers, anything calling into centaur_sdk). Aligned with upstream centaur conventions. Covers ruff/pytest discipline, modern type syntax, frozen dataclasses, classes vs. plain functions, imports, errors, secrets, HTTP, and tests.
---

# Python style guide

Elegance is low lines of code, high reliability, and low maintenance burden. Simple beats clever. Most complexity is added without being earned, and the cost lands later on whoever maintains it. The job of this skill is to refuse that complexity and produce code that is strongly typed, immutable by default, and obvious to read.

These rules describe how good Python is written in this codebase — they match upstream centaur. When existing overlay code disagrees, the overlay code is what we're trying to improve; fix it as you touch it.

## Simplicity first

**The core test.** Before adding any structure — a class, a factory, an exception subclass, a `Protocol`, a wrapper, a config layer, a dependency — ask: *what does this let me do that the simpler version can't?* If you can't name a concrete thing, don't add it. "Flexibility," "future-proofing," and "extensibility" are not concrete answers.

**Add the abstraction when it's forced, not before.** First case → write it directly. Second case → consider sharing. Genuine third case → abstract. Reaching for the `Protocol`, the factory, or the plugin registry on the first instance is speculative complexity. Adding it later is a small migration; adding it speculatively is permanent surface area.

Specific Python patterns to refuse unless something concrete forces them:

- A class that wraps a single function (`Manager`, `Service`, `Handler`, `Processor`). Use the function.
- A `Protocol` or `abc.ABC` for an interface with one implementer. Use the concrete type.
- A custom exception class for a failure no caller will ever catch. Use `ValueError` / `RuntimeError`.
- A plugin / registry / strategy pattern for two known cases. Use a `match` on a `Literal` tag.
- `**kwargs: Any` "for flexibility." Spell the parameters out, or use `TypedDict` + `Unpack`.
- A new module for one tiny helper. Co-locate it with its caller.
- A dependency that saves one line of code you could write inline.
- A wider dataclass with `| None` fields where a discriminated union is the actual shape.

When in doubt, write the simpler version. The migration when a second case forces your hand is cheap; the surface area of a speculative abstraction is forever.

## Stack

| Concern | Tool |
| --- | --- |
| Format + lint | `ruff` (config: `overlay/ruff.toml`) |
| Test runner | `pytest` |
| Async test driver | `pytest-asyncio` |
| HTTP client | `httpx` |
| Postgres | `asyncpg` (async) / `psycopg` (sync, only where forced) |
| HTTP/parse models | `pydantic` v2 |

Minimum Python: **3.11**. Line length: **100**. Quote style: double.

## `from __future__ import annotations` — use it

Add it as the first import of every `.py` file. It defers annotation evaluation, which keeps imports cheap, lets you reference types declared lower in the file without quoting, and matches the rest of the codebase.

```python
from __future__ import annotations

from dataclasses import dataclass

@dataclass
class Tree:
    parent: Tree | None
    children: list[Tree]
```

One trap: libraries that read `__annotations__` at runtime (`dataclasses_json`, `beartype`, some pydantic v1 patterns) can choke on deferred annotations. Pydantic v2 resolves forward refs itself and is fine. If a specific module mixes with a library that breaks, drop the import *in that module only* and quote forward references.

## Type syntax

Use PEP 585 / 604 everywhere. Never import `List`, `Dict`, `Tuple`, `Optional`, `Union` from `typing`.

```python
def head(xs: list[int], m: dict[str, float]) -> tuple[int, str] | None: ...
```

PEP 695 generics (`def f[T](xs: list[T]) -> T`, `type Vector = ...`) for new code on 3.12+. `TypeVar` is fine in modules pinned to 3.11.

Annotate function parameters, public return types, and module-level constants. Skip local-variable annotations when inference is unambiguous. Annotate locals when inference can't figure out an empty container (`seen: set[str] = set()`, `out: list[str] = []`).

## Data containers

**Invariant.** Every dataclass that holds value-typed data is `frozen=True`. It makes input-mutation bugs a runtime error and costs nothing.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    timeout_s: float = 30.0
    retries: int = 3

cfg = Config(timeout_s=10.0)
new_cfg = replace(cfg, retries=5)
```

Plain `@dataclass` (mutable) is acceptable when the object is genuinely mutable shared state (e.g., a `ToolContext` populated at request time, a `*Info` record returned from a DB query and then enriched). Don't reach for `frozen=True` reflexively if the surrounding code doesn't.

Add `slots=True` when you've seen attribute typos cause bugs or have measured memory pressure. Watch out for interactions with multi-inheritance, `cached_property`, and `Exception`.

Add `kw_only=True` when the field count crosses ~4 and positional construction becomes unreadable. For two- or three-field value types, positional construction is cleaner.

`pydantic.BaseModel` is for parsing untrusted input (HTTP request/response, YAML, CLI args) — never for internal state.

## No input mutation

Functions never mutate their inputs. Return a new value built with `dataclasses.replace(...)` or by constructing a fresh container. `frozen=True` enforces this for dataclasses; for arrays, dicts, and lists, the rule is the only guard.

```python
# CORRECT
def normalise(xs: np.ndarray) -> np.ndarray:
    return xs / xs.sum()

# INCORRECT — mutates caller's array
def normalise(xs: np.ndarray) -> None:
    xs /= xs.sum()
```

## Classes vs. plain functions

Default to **module-level functions** operating on frozen dataclasses. A class earns its keep only when there's *genuine instance state* the same object holds across multiple calls:

- A client that owns connection/auth state (API keys, base URL, retry config, an `httpx.AsyncClient`).
- A long-lived resource manager.
- A discriminated union of subclasses (rare; usually `Literal` tags are better).

```python
# CORRECT — class for a stateful client
class WebSearchClient:
    def __init__(self, *, api_key: str, base_url: str, max_retries: int = 3) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries

    async def search(self, query: str) -> SearchResponse: ...
    async def deep_research(self, query: str) -> DeepResearchResponse: ...

# CORRECT — plain functions for stateless ops
def generate_key() -> tuple[str, str, str]: ...
def hash_key(key: str) -> str: ...
```

When you do define a class:

- Give it a **noun** name (`Client`, `Context`, `Spec`), not `Manager`/`Service`/`Handler`.
- Give methods **verb** names (`client.search(...)`, not `client.do_search(...)` or `client.search_handler(...)`).
- Don't repeat the class noun in method names: `cache.put(...)`, not `cache.put_in_cache(...)`.
- Put validation and side effects in module-level factory functions or in `__init__`, not in classmethods you have to remember to call.

For pure-data classes (config, results, value types), use a `@dataclass(frozen=True)` and skip the class body entirely.

## Construction

Plain `__init__` is the default — `WebSearchClient(api_key=...)` is fine. Reach for a module-level factory function only when construction has real logic the caller shouldn't see (loading + validation, multi-source defaults, async initialisation that `__init__` can't express). When you do write one, name it for its source:

| Source | Name |
| --- | --- |
| A typed `Config` | `create(config)` |
| Serialized blob | `from_bytes`, `from_dict`, `from_json`, `from_path` |
| Loose positional fields | `make(...)` |

Avoid `build` / `construct` / `init` / `new` — the names above are clearer.

## Discriminated unions

Variants with different shapes get tagged with a `Literal` and matched on the tag. Don't pile `| None` fields onto one wide dataclass or BaseModel.

```python
class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    source: Base64Source

ContentBlock = TextBlock | ImageBlock

def render(b: ContentBlock) -> str:
    match b:
        case TextBlock(text=t): ...
        case ImageBlock(source=s): ...
```

Prefer `Literal` unions over `enum.Enum` for closed sets that mostly exist to label data. `enum.Enum` is fine when the values need methods, a stable wire format with named members (`SecretMode(str, Enum)`), or hierarchical typing.

## Parameters

- Keyword-only (`*`) for everything beyond one or two obvious positionals.
- No mutable defaults — use `None` then assign, or `field(default_factory=...)`.
- More than four related parameters → a frozen-dataclass `Config`, not a longer signature.

## Imports

1. Absolute imports only. `from .core import x` is forbidden.
2. No `*` imports.
3. Ruff isort: stdlib → third-party → first-party, one blank line between groups.
4. `if TYPE_CHECKING:` for imports used only in annotations.

## Secrets and HTTP

These bans are enforced by `overlay/ruff.toml` and `.centaur/tools/ruff.toml`, scoped to **tool packages** (`overlay/tools/<tool>/`, `.centaur/tools/...`). Service code (`overlay/centaur_lab/`, `.centaur/services/api/`) reads its config from environment variables.

In tool code:

- **No `os.getenv` / `os.environ.get`.** Use `from centaur_sdk import secret; secret("KEY")`. Secrets come from the sidecar, not the process environment.
- **No `requests`.** Use `httpx`. `httpx` respects `HTTPS_PROXY` for firewall credential injection; `requests` does not.
- **No `dotenv.load_dotenv`.** Tools receive secrets through the secret manager, not `.env` files.

`*/cli.py` is exempt from the `print()` and `load_dotenv` bans — CLIs are standalone entrypoints.

## Strings

f-strings for interpolation. `%`-style only for stdlib logging (lazy formatting).

```python
msg = f"loss={loss:.4f} step={step}"
log.info("loss=%.4f step=%d", loss, step)
```

## Errors

1. Plain `raise RuntimeError(...)` / `raise ValueError(...)` is the default. The custom hierarchy below is only worth it when callers will programmatically distinguish errors.
2. When a package's public surface has **multiple related error types** that callers will catch separately, define one package base (`<Pkg>Error(Exception)`) and have the others inherit from it. Multi-inherit a stdlib type when semantically useful: `ConfigError(<Pkg>Error, ValueError)`.
3. Always chain across boundaries: `raise X from Y`. Use `from None` only to intentionally hide the cause.
4. Never use bare `except:` or `except Exception:`. Catch the narrowest type.
5. Build the message in a local variable (Ruff `EM`).
6. When an exception is raised across a boundary and callers may want structured access, carry typed fields — not just a message string.
7. `assert` is for internal invariants only — never for validating user input.

```python
# Most cases — plain stdlib exception
if not api_key:
    raise RuntimeError("EXA_API_KEY not set.")

# Single one-off domain error — inherit from stdlib
class SlackAuthError(RuntimeError):
    """Raised when Slack rejects the bot token."""

# Multi-error public surface — package base + structured fields
class TwitterSDKError(Exception):
    """Base for all Twitter SDK errors."""

class RateLimitError(TwitterSDKError):
    def __init__(self, msg: str, *, retry_after_s: float) -> None:
        super().__init__(msg)
        self.retry_after_s = retry_after_s
```

Don't define a `<Pkg>Error` base for one subclass — use `RuntimeError` directly.

## Paths and I/O

`pathlib.Path` only — `os.path` is forbidden (Ruff `PTH`). `print()` is forbidden outside `*/cli.py`; use `logging` everywhere else.

## Testing

- Tests live in `tests/` next to the package. No `tests/__init__.py`.
- File names `test_*.py`; functions `test_*`.
- Async tests use `pytest-asyncio`. Some packages configure `asyncio_mode = "strict"` (every async test decorated); some use the default auto mode. Match the package you're in.
- Prefer `@pytest.mark.parametrize` over loops when only inputs vary.
- Fixtures only for setup/teardown of real resources; narrowest scope that works.
- Fixed seeds for randomized tests.

```python
@pytest.mark.asyncio
async def test_upload() -> None: ...

@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(1, 2, 3), (0, 0, 0), (-1, 1, 0)],
)
def test_add(a: int, b: int, expected: int) -> None:
    assert add(a, b) == expected
```
