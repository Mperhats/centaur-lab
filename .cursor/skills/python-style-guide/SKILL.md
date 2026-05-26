---
name: python-style-guide
description: Use when writing, editing, or reviewing any Python in this repo. Covers ruff/pyright/pytest discipline, modern type syntax, frozen dataclasses, module-as-namespace + verb-only function names, imports, errors, secrets, HTTP, and tests. These are invariants — when existing code disagrees, the existing code is wrong and should be fixed as you touch it.
---

# Python style guide

Elegance is low lines of code, high reliability, and low maintenance burden. Simple beats clever. Most complexity is added without being earned, and the cost lands later on whoever maintains it. The job of this skill is to refuse that complexity and produce code that is strongly typed, immutable by default, and obvious to read.

These are invariants, not preferences. When you encounter code that breaks them, fix it as you touch the file. Don't pattern-match on the surrounding mistakes — the surrounding code is what we're trying to improve.

## Simplicity first

**The core test.** Before adding any structure — a class, a factory, an exception subclass, a `Protocol`, a wrapper, a config layer, a dependency — ask: *what does this let me do that the simpler version can't?* If you can't name a concrete thing, don't add it. "Flexibility," "future-proofing," and "extensibility" are not concrete answers.

**Add the abstraction when it's forced, not before.** First case → write it directly. Second case → consider sharing. Genuine third case → abstract. Reaching for the `Protocol`, the factory, or the plugin registry on the first instance is speculative complexity. Adding it later is a small migration; adding it speculatively is permanent surface area.

Specific Python patterns to refuse unless something concrete forces them:

- A class that wraps a single function (`Manager`, `Service`, `Handler`, `Processor`). Use the function.
- A `@classmethod` factory when a module-level `create(config)` / `from_<source>(...)` would do.
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
| Type check | `pyright` in `strict` mode |
| Test runner | `pytest` |
| Async test driver | `pytest-asyncio` (`asyncio_mode = "strict"`) |
| HTTP client | `httpx` |
| Postgres | `asyncpg` (async) / `psycopg` (sync, only where forced) |

Minimum Python: **3.11**. Line length: **100**. Quote style: double.

## `from __future__ import annotations` — forbidden

Do not add it. Remove it when you touch a file that has it. It defers annotation evaluation, which breaks runtime introspection used by `dataclasses_json`, `pydantic`, `dataclasses` with forward references, and any decorator that reads `__annotations__`. PEP 649/749 supersedes the original PEP 563 motivation. PyTorch removed every occurrence for this reason.

Quote forward references when you need them:

```python
class Tree:
    parent: "Tree | None"
    children: list["Tree"]
```

## Type syntax

Use PEP 585 / 604 everywhere. Never import `List`, `Dict`, `Tuple`, `Optional`, `Union` from `typing`.

```python
def head(xs: list[int], m: dict[str, float]) -> tuple[int, str] | None: ...
```

PEP 695 generics (`def f[T](xs: list[T]) -> T`, `type Vector = ...`) for new code on 3.12+. `TypeVar` is forbidden in new code.

Annotate exactly three things: function parameters, public return types, and module- or class-level constants. Let pyright infer locals — redundant local annotations drift; inferred types stay correct under refactoring.

## Data containers

**Invariant.** Every dataclass is `frozen=True`. It makes input-mutation bugs a runtime error and costs nothing.

```python
@dataclass(frozen=True)
class Config:
    timeout_s: float = 30.0
    retries: int = 3

cfg = Config(timeout_s=10.0)
new_cfg = replace(cfg, retries=5)
```

Add `slots=True` when you've seen attribute typos cause bugs or have measured memory pressure. Watch out for interactions with multi-inheritance, `cached_property`, and `Exception` — `slots` is not universally free.

Add `kw_only=True` when the field count crosses ~4 and positional construction becomes unreadable. For two- or three-field value types, positional construction is cleaner.

`pydantic.BaseModel` is for parsing untrusted input (HTTP, YAML, CLI) only — never for internal state. When used, set `model_config = ConfigDict(frozen=True, extra="forbid")`.

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

## Module-as-namespace + verb-only names

Design every module so consumers import the *module* and call its verbs through it. The module carries the noun; the function carries the verb. A function named `create_cache` inside a module named `cache` says "cache" twice at every call site.

```python
# CORRECT
from centaur_lab import cache

c = cache.create(size=1024)
c = cache.put(c, key="x", value=42)

# INCORRECT
from centaur_lab.cache import create_cache, put_in_cache
```

Booleans keep their auxiliary verb: `is_terminal`, `has_credential`, `should_resample`. No type-decoration suffixes (`_fn`, `_func`, `_handler`).

## Factory functions

Public construction goes through a module-level function, not heavy work in `__init__`.

| Source | Name |
| --- | --- |
| A typed `Config` | `create(config)` |
| Serialized blob | `from_bytes`, `from_dict`, `from_json`, `from_path` |
| Loose positional fields | `make(...)` |

`build`, `construct`, `init`, `new` are forbidden — the names above cover every case.

## Discriminated unions

Variants with different shapes get tagged with a `Literal` and matched on the tag. Never pile `| None` fields onto one wide dataclass.

```python
@dataclass(frozen=True)
class HashPayload:
    type: Literal["hash"] = "hash"
    hash: bytes

@dataclass(frozen=True)
class TxPayload:
    type: Literal["transaction"] = "transaction"
    signature: bytes

Payload = HashPayload | TxPayload

def settle(p: Payload) -> Receipt:
    match p:
        case HashPayload(hash=h): ...
        case TxPayload(signature=s): ...
```

The `Literal` tag is what makes `match` exhaustive and lets pyright narrow each branch. `enum.Enum` is forbidden; `Literal` unions and maps cover every case.

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

Two hard bans enforced by `overlay/ruff.toml`:

- **No `os.getenv` / `os.environ.get`** in tool code. Use `from centaur_sdk import secret; secret("KEY")`.
- **No `requests`.** Use `httpx`. `httpx` respects `HTTPS_PROXY` for firewall credential injection; `requests` does not.

## Strings

f-strings for interpolation. `%`-style only for stdlib logging (lazy formatting).

```python
msg = f"loss={loss:.4f} step={step}"
log.info("loss=%.4f step=%d", loss, step)
```

## Errors

1. Every package with a public surface defines one base exception (`<Pkg>Error(Exception)`); every custom error inherits from it. Subclass stdlib types where semantically useful: `ConfigError(<Pkg>Error, ValueError)`.
2. Always chain across boundaries: `raise X from Y`. Use `from None` only to intentionally hide the cause.
3. Never use bare `except:` or `except Exception:`. Catch the narrowest type.
4. Build the message in a local variable (Ruff `EM`).
5. Exceptions raised across a module boundary carry typed fields, not just a message string. The message is for humans; the fields are for callers.
6. `assert` is for internal invariants only — never for validating user input.

```python
class ShapeError(ToolError, ValueError):
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

if grads.shape != params.shape:
    msg = f"gradient shape {grads.shape} != parameter shape {params.shape}"
    raise ShapeError(msg, expected=params.shape, actual=grads.shape)
```

Inside one package, plain `raise ValueError(...)` / `raise RuntimeError(...)` is fine for purely-internal failures that no caller will ever catch. The custom hierarchy is required at the package's *public* surface.

## Paths and I/O

`pathlib.Path` only — `os.path` is forbidden (Ruff `PTH`). `print()` is forbidden outside `*/cli.py`; use `logging` everywhere else.

## Testing

- Tests live in `tests/` next to the package. No `tests/__init__.py`.
- File names `test_*.py`; functions `test_*`.
- Async tests use `pytest-asyncio` in `strict` mode — every async test is decorated explicitly.
- Prefer `@pytest.mark.parametrize` over loops when only inputs vary.
- Fixtures only for setup/teardown of real resources; narrowest scope that works.
- Fixed seeds for randomized tests.
- `filterwarnings = ["error"]` in `pyproject.toml` so deprecation warnings fail the suite.

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
