---
name: python-style-guide
description: Use when writing, editing, or reviewing Python code in a JAX-based ML research library. Covers code style, naming, API design (module-as-namespace, verb-only function names, canonical factory names like `create`/`from_*`/`make`), discriminated unions with `Literal` tags, structured exception fields, immutability and no-input-mutation, formatting (ruff format), linting (ruff), type checking (pyright strict), modern type syntax (PEP 585/604/695), Google-style docstrings, imports, pytest, and error handling.
---

# Python style guide

A single prescribed style. These are invariants, not suggestions. Follow them literally.

## Scope — read this first

These rules are written for **greenfield, publishable Python libraries** (the
canonical example: a JAX-based ML research library going to PyPI). For
**internal services** that ship as a deployment (not a wheel), the host
project's conventions take precedence over anything here. Concretely, in the
centaur-lab repo:

| Topic | Greenfield rule (below) | Centaur convention (`.centaur/`, `overlay/tools/`) |
| --- | --- | --- |
| `from __future__ import annotations` | Forbidden (PEP 649 supersedes) | Required at the top of every file |
| Ruff rule selection | Broad: ~30 rule families with `D`, `S`, `EM`, `PT`, `PL`, `N818`, etc. | Narrow: `["E", "F", "I", "UP", "B", "SIM", "RUF", "TID", "T201"]` |
| Line length | 88 | 100 |
| Custom exception hierarchy with structured fields | Required at module boundaries | Plain `raise RuntimeError(...)` / `raise ValueError(...)` in workflows; custom `XxxError` only at the API surface |
| `Final[T]` on constants | Required | Not used; plain `MODULE_CONST = value` |
| `__all__` | Required on every `__init__.py` | Used on public-API `__init__.py` only (e.g. `centaur_sdk`) |
| `@dataclass(frozen=True, slots=True, kw_only=True)` everywhere | Yes | Plain `@dataclass` is the default; `frozen=True, slots=True` for genuinely immutable value types; `kw_only=True` only when a signature is large |
| `os.environ.get` | Forbidden in libraries (use config object) | Used directly in workflows; banned only in `overlay/tools/` where `centaur_sdk.secret()` is the alternative (see `overlay/tools/ruff.toml`) |
| `print()` in library code | Forbidden | Allowed in `*/cli.py` and other clearly-named CLI entrypoints |
| Verb-only function names | Required (e.g. `cache.put`, not `put_in_cache`) | Pragmatic — `bootstrap_service_api_keys`, `seed_channel_bootstrap_job` are fine when the noun aids clarity |
| Pyright | `strict` | `basic` for internal tooling; `strict` for the SDK |

**Rule for the agent**: before applying any "invariant" below to centaur code,
check whether the project already does something else. Match the project. If
you're writing a new top-level library that will be published, the invariants
below apply unchanged.

## Stack (no alternatives)

| Concern              | Tool                                            |
| -------------------- | ----------------------------------------------- |
| Formatter            | `ruff format`                                   |
| Linter / isort       | `ruff check`                                    |
| Type checker         | `pyright` in `strict` mode                      |
| Test runner          | `pytest`                                        |
| Async test driver    | `anyio` (`@pytest.mark.anyio`) — never `pytest-asyncio` |
| Property testing     | `hypothesis`                                    |
| Snapshot testing     | `syrupy` (only when needed)                     |
| Time freezing        | `pytest-freezer`                                |
| Coverage             | `coverage.py` with `branch = true`              |

Minimum Python: **3.11**. PEP 695 features (inline generics, `type` aliases) require **3.12+** — prefer them when the project pins 3.12+.

## Formatting

**Invariant.** Format every file with `ruff format`. Line length is **88**. Quote style is **double**. Indent is **4 spaces**.

Do not hand-format. Do not introduce a `# fmt: off` block unless documenting a numeric matrix that loses meaning when reflowed.

```toml
# pyproject.toml
[tool.ruff]
line-length = 88
target-version = "py311"

[tool.ruff.format]
quote-style = "double"
docstring-code-format = true
docstring-code-line-length = 72
```

## Linting

**Invariant.** Enable this exact rule selection. Do not add or remove rules without a written justification in the PR.

```toml
[tool.ruff.lint]
select = [
  "E", "W", "F", "I", "B", "UP", "SIM", "RUF", "C4", "PTH",
  "PIE", "PT", "PYI", "RET", "RSE", "TID", "TC", "ARG", "PL",
  "PERF", "FURB", "NPY", "N", "D", "S", "T20", "EM", "G",
  "ICN", "ISC",
]
ignore = [
  "E501", "ISC001", "COM812",   # formatter conflicts
  "PLR0913", "PLR2004",          # common in ML
  "D203", "D213",                # docstring style conflicts
  "S101",                        # asserts allowed in tests
]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.isort]
known-first-party = ["mylib"]
combine-as-imports = true
split-on-trailing-comma = true

[tool.ruff.lint.flake8-tidy-imports]
ban-relative-imports = "all"

[tool.ruff.lint.flake8-import-conventions.aliases]
numpy = "np"
"jax.numpy" = "jnp"

[tool.ruff.lint.per-file-ignores]
"tests/**"           = ["D", "S101", "PLR2004", "ARG", "ANN", "T20"]
"**/__init__.py"     = ["F401"]
```

## Type checking

**Invariant.** Type-check with `pyright` in `strict` mode. Ship a `py.typed` marker file inside the package. Public APIs must contain **zero `Any`**. Use `# pyright: ignore[ruleName]` only with a reason in the same comment.

```toml
[tool.pyright]
include = ["src", "tests"]
pythonVersion = "3.11"
typeCheckingMode = "strict"
reportMissingTypeStubs = "warning"
reportUnnecessaryTypeIgnoreComment = "warning"
reportImplicitOverride = "error"
venvPath = "."
venv = ".venv"
```

`ty` (Astral) is not stable as of mid-2026. Do not adopt it as the primary type checker yet.

## Type syntax

**Invariant.** Use modern syntax everywhere. Never import the deprecated typing aliases.

```python
# CORRECT — PEP 585 + PEP 604
def head(xs: list[int], m: dict[str, float]) -> tuple[int, str] | None: ...

# INCORRECT
from typing import List, Dict, Tuple, Optional, Union
def head(xs: List[int], m: Dict[str, float]) -> Optional[Union[Tuple[int, str]]]: ...
```

```python
# CORRECT — PEP 695 (Python 3.12+)
type Vector = jax.Array
type Batch[T] = list[T]

def first[T](xs: list[T]) -> T:
    return xs[0]

class Container[T]:
    def __init__(self, item: T) -> None:
        self.item = item

# INCORRECT — legacy TypeVar / TypeAlias
from typing import TypeVar, TypeAlias
T = TypeVar("T")
Vector: TypeAlias = "jax.Array"
```

`Self`, `TypedDict`, `NamedTuple`, `Protocol`:

```python
from typing import Self, TypedDict, NamedTuple, Protocol, runtime_checkable

class Model:
    def to(self, device: str) -> Self: ...

class Config(TypedDict):
    learning_rate: float
    batch_size: int

class Range(NamedTuple):
    lo: int
    hi: int

@runtime_checkable
class LossFn(Protocol):
    def __call__(self, params: PyTree, batch: Batch) -> jax.Array: ...
```

**Protocol vs ABC.** Use `Protocol` for any interface consumed across a module boundary. Use `abc.ABC` only when you want subclasses to nominally inherit and share concrete code. For public APIs in this library, `Protocol` is almost always correct.

### `from __future__ import annotations` — forbidden

**Invariant.** Do not add `from __future__ import annotations` to any file in this project.

Reasons: PEP 649/749 supersedes PEP 563. It breaks runtime introspection used by `jaxtyping`, `beartype`, `pydantic`, and `dataclasses` with forward references. PyTorch removed every occurrence for this reason.

```python
# CORRECT — quote the forward reference
class Tree:
    parent: "Tree | None"
    children: list["Tree"]

# INCORRECT
from __future__ import annotations
class Tree:
    parent: Tree | None
```

### Data containers — prescribed split

| Use case                                                | Tool                                                       |
| ------------------------------------------------------- | ---------------------------------------------------------- |
| Plain data, internal config, ML hyperparameters         | `@dataclass(frozen=True, slots=True, kw_only=True)`        |
| JAX PyTree-bearing model state                          | `eqx.Module` (see `jax-best-practices.md`)                 |
| Trust-boundary parsing (CLI, YAML, JSON, HTTP)          | `pydantic.BaseModel` with `model_config = ConfigDict(frozen=True, extra="forbid")` |

Do not use `attrs` in this project; stdlib `dataclasses` covers every internal need, and Equinox covers the JAX side. Pydantic is for untrusted input only — never for ML state.

```python
# CORRECT
from dataclasses import dataclass

@dataclass(frozen=True, slots=True, kw_only=True)
class TrainConfig:
    learning_rate: float
    batch_size: int
    seed: int = 0
```

## Naming

| Entity                      | Convention          | Example              |
| --------------------------- | ------------------- | -------------------- |
| Module, package, function   | `snake_case`        | `mylib.optim.sgd`    |
| Class, TypeVar, type alias  | `PascalCase`        | `TrainState`, `Vector` |
| Constant                    | `UPPER_SNAKE_CASE`  | `DEFAULT_LR`         |
| Module-private              | `_leading_underscore` | `_helper`          |
| Name-mangled (rare)         | `__double_leading`  | `__private`          |

Single-letter names (`l`, `I`, `O`) are forbidden. ML-conventional short names (`x`, `y`, `w`, `b`, `lr`) are allowed inside small numeric functions but never in public signatures.

## API design and naming

These rules govern how to *shape and name* the things you put in a module, beyond the lexical conventions in the table above. They target a wevm/viem-style API surface adapted for Python: small, namespace-imported modules with short verb-only functions that operate on plain frozen data. Structural rules (where modules live, what counts as the public API) live in `python-library-best-practices.md` — this section is the naming half.

### Module-as-namespace

**Invariant.** Design every domain module so consumers import the *module* and call its functions through it. Do not flat-export name-prefixed helpers.

```python
# CORRECT — namespace import, verb-only function names
from mylib import cache

c = cache.create(size=1024)
c = cache.put(c, key="x", value=42)
v = cache.get(c, key="x")

# INCORRECT — flat exports with redundant prefixes
from mylib.cache import create_cache, put_in_cache, get_from_cache

c = create_cache(size=1024)
c = put_in_cache(c, key="x", value=42)
v = get_from_cache(c, key="x")
```

Rules:

1. **The module carries the noun. The function carries the verb.** A function named `create_cache` inside a module named `cache` says "cache" twice at every call site.
2. **Short module names.** Prefer `cache` over `cache_store`, `optim` over `optimizer_state`. Multi-word module names propagate into every line that touches them.
3. **Re-export subpackages as namespaces.** In `src/mylib/__init__.py` write `from mylib import cache as cache` so users can `import mylib; mylib.cache.create(...)`. (See `python-library-best-practices.md` §`__init__.py and the public API`.)
4. **Co-locate types with the functions that consume them.** `cache.State`, `cache.Config` live in `cache.py` next to `cache.create` and `cache.put`. Don't hoist them into a separate `types.py` unless they are genuinely cross-module.

### Factory functions and canonical names

**Invariant.** Public construction goes through a module-level factory function with a frozen-dataclass config. Reserve `__init__` for plain-data classes (frozen dataclasses) and for genuine subclassing hierarchies (exceptions, `Protocol` impls, `eqx.Module`).

```python
# CORRECT
@dataclass(frozen=True, slots=True, kw_only=True)
class Config:
    learning_rate: float
    momentum: float = 0.9
    weight_decay: float = 0.0

def create(config: Config) -> State:
    """Create an optimiser state from the given configuration."""
    ...

# Callsite
from mylib import optim
state = optim.create(optim.Config(learning_rate=1e-3))

# INCORRECT — constructor doing real work
class Optimizer:
    def __init__(self, learning_rate: float, momentum: float = 0.9) -> None:
        ...  # validation, JAX compilation, parameter wiring
```

Canonical factory names — pick the one that matches the source:

| Source                        | Name                                       |
| ----------------------------- | ------------------------------------------ |
| A typed `Config` object       | `create`                                   |
| A serialized blob             | `from_bytes`, `from_dict`, `from_json`, `from_path` |
| Loose positional fields       | `make`                                     |
| A `cls`-bound alt-constructor | `@classmethod def from_<thing>(cls, ...)` (rare) |

`from` alone is a Python keyword; always use the `from_<source>` suffix. Avoid `build`, `construct`, `init`, `new` — the four names above cover every case.

### Function naming inside a module

**Invariant.** Inside a module, name functions with **the verb only**. Do not repeat the module's noun. Do not add type-decoration suffixes (`_fn`, `_func`, `_handler`, `_method`).

| Module               | Good                                       | Bad                                                          |
| -------------------- | ------------------------------------------ | ------------------------------------------------------------ |
| `mylib.optim`        | `create`, `step`, `schedule`, `zero_grad`  | `create_optimizer`, `optimizer_step`, `optimizer_schedule`   |
| `mylib.checkpoint`   | `save`, `load`, `list`, `latest`           | `save_checkpoint`, `load_checkpoint`, `list_checkpoints`     |
| `mylib.cache`        | `create`, `get`, `put`, `evict`, `size`    | `create_cache`, `cache_get`, `put_in_cache`                  |
| `mylib.exceptions`   | (class names only)                         | `make_error_*`, `raise_error_*`                              |

Booleans keep the auxiliary verb: `is_terminal`, `has_credential`, `should_resample`. Predicate names are exempt from "verb only".

### Minimal variable names

**Invariant.** Inside a function body, prefer the shortest name that is unambiguous given the surrounding context. Do not re-state the module's domain in every local — the module name, the parameter name, and the type already disambiguate.

```python
# CORRECT
def step(state: State, *, grads: jax.Array, learning_rate: float) -> State:
    velocity = state.momentum * state.velocity + grads
    params = state.params - learning_rate * velocity
    return replace(state, params=params, velocity=velocity, step=state.step + 1)

# INCORRECT — every name re-states the domain
def optimizer_step(
    optimizer_state: State,
    *,
    new_grads: jax.Array,
    optimizer_lr: float,
) -> State:
    new_velocity = optimizer_state.momentum * optimizer_state.velocity + new_grads
    new_params = optimizer_state.params - optimizer_lr * new_velocity
    return replace(
        optimizer_state,
        params=new_params,
        velocity=new_velocity,
        step=optimizer_state.step + 1,
    )
```

The single-letter ban still holds at *public signatures*: no `x`, `b`, `k` in the parameters of an exported function. Inside the body, short names that match the type's role (`g` for grads, `lr` for learning rate, `cfg` for config) are encouraged when the surrounding context disambiguates.

### Discriminated unions over optional fields

**Invariant.** When a payload has variants with different shapes, model it as a discriminated union keyed by a `Literal` tag — not as one wide dataclass full of `| None` fields.

```python
# CORRECT — closed, exhaustive, pyright-narrowable
from typing import Literal

@dataclass(frozen=True, slots=True, kw_only=True)
class HashPayload:
    type: Literal["hash"] = "hash"
    hash: bytes

@dataclass(frozen=True, slots=True, kw_only=True)
class TxPayload:
    type: Literal["transaction"] = "transaction"
    signature: bytes

type Payload = HashPayload | TxPayload

def settle(p: Payload) -> Receipt:
    match p:
        case HashPayload(hash=h): ...
        case TxPayload(signature=s): ...

# INCORRECT — wide bag of optionals; impossible to make exhaustive
@dataclass(frozen=True, slots=True, kw_only=True)
class Payload:
    type: str
    hash: bytes | None = None
    signature: bytes | None = None
```

The `Literal` tag is what makes `match` exhaustive and what lets pyright narrow each branch automatically. For string-only variants without payload, use a `Literal` alias directly:

```python
type Intent = Literal["charge", "session"]
```

(Per existing rule: `enum.Enum` is forbidden. Maps and `Literal` unions cover every case.)

### Let pyright infer

**Invariant.** Annotate exactly three things: function parameters, public return types, and module- or class-level constants where the literal type would be too narrow. Do not annotate local variables that pyright can infer.

```python
# CORRECT
MAX_GRAD_NORM: Final[float] = 1.0

def create(config: Config) -> State:
    params = _init_params(config)         # inferred jax.Array
    transitions = _compile(config)         # inferred Callable[..., State]
    return State(params=params, step=transitions)

# INCORRECT — restating the inferred type
def create(config: Config) -> State:
    params: jax.Array = _init_params(config)
    transitions: Callable[..., State] = _compile(config)
    return State(params=params, step=transitions)
```

Redundant local annotations drift as code changes; inferred types stay correct under refactoring. The `ARG`, `RET`, and `ANN` ruff selections enforce the boundary; pyright catches drift.

## Docstrings

**Invariant.** Google-style docstrings on every public module, class, function, and method. Enforced by `D` rules with `convention = "google"`. Summary line is imperative mood, one line, ends with a period.

```python
def compute_loss(
    params: PyTree,
    batch: Batch,
    *,
    reduction: Literal["mean", "sum"] = "mean",
) -> jax.Array:
    """Compute the cross-entropy loss for a batch.

    Args:
        params: Model parameters as a JAX PyTree.
        batch: Input batch with `inputs` of shape ``(B, D)`` and integer
            `labels` of shape ``(B,)``.
        reduction: How to reduce over the batch dimension.

    Returns:
        A scalar JAX array containing the loss.

    Raises:
        ShapeError: If `batch.inputs` and `batch.labels` have mismatched
            leading dimensions.
    """
```

Do not restate types already in the signature unless adding shape or unit information. Tests have no docstrings.

## Imports

**Invariant.**

1. **Absolute imports only.** `from .core import x` is forbidden.
2. **No star imports.**
3. Sort with `ruff` isort: stdlib → third-party → first-party, one blank line between groups.
4. `if TYPE_CHECKING:` for imports used only in annotations.
5. Re-exports in `__init__.py` use `from .x import Name as Name` and are listed in `__all__`.

```python
# CORRECT — src/mylib/__init__.py
"""Public API for mylib."""
from mylib._core import Model as Model
from mylib._core import train_step as train_step
from mylib.exceptions import MylibError as MylibError

__all__ = ["Model", "MylibError", "train_step"]
```

```python
# INCORRECT
from .core import *
from .core import Model        # implicit re-export — pyright strict will reject
```

## Strings

**Invariant.** f-strings for interpolation. `%`-style only for stdlib logging (lazy formatting). Never `.format()`, never `+`-concatenation, never `%` outside logging.

```python
# CORRECT
msg = f"loss={loss:.4f} step={step}"

# CORRECT — logging is lazy
log.info("loss=%.4f step=%d", loss, step)

# INCORRECT
msg = "loss=%.4f step=%d" % (loss, step)
log.info(f"loss={loss:.4f} step={step}")
```

## Error handling

**Invariant.**

1. Every custom exception inherits from a single library base (`MylibError`).
2. Always chain with `raise X from Y`; use `from None` only to intentionally hide a cause.
3. Build error messages in a local variable (Ruff `EM`).
4. Never use bare `except:` or `except Exception:`. Catch the narrowest type.
5. `assert` is for internal invariants only — never for validating user input.

```python
# CORRECT
try:
    data = json.loads(path.read_text())
except json.JSONDecodeError as exc:
    msg = f"Invalid config at {path!r}"
    raise ConfigError(msg) from exc

# INCORRECT
try:
    data = json.loads(path.read_text())
except Exception:
    raise ConfigError("bad config")
```

## Paths & I/O

**Invariant.** `pathlib.Path` only. `os.path` is forbidden (Ruff `PTH`). `print()` is forbidden in library code (Ruff `T20`) — use `logging` outside JAX, `jax.debug.print` inside traced functions.

## Testing

**Invariant.** Test runner is `pytest`. Tests live in `tests/` at the repo root, **without** an `__init__.py`. File names are `test_*.py`; functions are `test_*`.

```toml
[tool.pytest.ini_options]
minversion = "8.0"
addopts = ["-ra", "--strict-markers", "--strict-config", "--import-mode=importlib"]
testpaths = ["tests"]
xfail_strict = true
filterwarnings = ["error"]
```

Rules:

- **Prefer `@pytest.mark.parametrize` over loops** when only inputs vary.
- **Fixtures** only for setup/teardown of resources; narrowest scope that works.
- **Async tests use `anyio`**, not `pytest-asyncio`:

  ```python
  @pytest.mark.anyio
  async def test_upload() -> None:
      ...
  ```
- **Hypothesis** for property-based tests over array shapes, dtypes, hyperparameters.
- **`syrupy`** for snapshotting symbolic output (HLO, logged metric dicts). Never snapshot raw numeric arrays — use `chex.assert_trees_all_close` with tolerances.
- **Fixed seeds.** Every randomized test calls `jax.random.key(seed)` with a literal seed.
- Coverage uses **branch coverage**.

```python
@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(1, 2, 3), (0, 0, 0), (-1, 1, 0)],
)
def test_add(a: int, b: int, expected: int) -> None:
    assert add(a, b) == expected
```

## Quick-reference checklist

1. `ruff format`, 88 columns, double quotes.
2. `ruff check` with the prescribed selection; zero violations.
3. `pyright --strict`; zero errors; `py.typed` shipped.
4. Python 3.11+ syntax: `X | Y`, `list[int]`, `type Foo = ...`, `def f[T](x: T)`, `Self`.
5. No `from __future__ import annotations`.
6. Google-style docstrings on every public symbol.
7. Absolute imports; no `*`; explicit re-exports with `as` and `__all__`.
8. `@dataclass(frozen=True, slots=True, kw_only=True)` for data; `Protocol` for interfaces.
9. f-strings for interpolation; `%` only for logging.
10. Custom exception hierarchy rooted at `MylibError`; always `raise X from Y`.
11. `pathlib.Path` only; no `print` in library code.
12. pytest + anyio + hypothesis + branch coverage; tests have no `__init__.py`.
13. Module-namespace API: function names do not repeat the module noun (`cache.put`, not `put_in_cache`); short module names; co-locate types with the functions that consume them.
14. Public construction via factory functions: `create(config)`, `from_bytes(b)`, `from_path(p)`, `make(...)` — never heavy logic in `__init__`.
15. Discriminated unions with a `Literal["..."]` tag over wide dataclasses full of `\| None` fields; `match` on the tag.
16. Annotate parameters, public return types, and `Final` constants only; let pyright infer locals.
