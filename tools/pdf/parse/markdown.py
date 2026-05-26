"""PDF-bytes-to-Markdown via a three-stage fallback chain.

Real-world PDFs are wildly heterogeneous: born-digital LaTeX, scanned
images, weird CMaps, malformed xref tables. No single parser handles
all of them, so we cascade — best fidelity first, most permissive last:

    pymupdf4llm  →  pymupdf  →  pypdf

``pymupdf4llm`` produces the cleanest Markdown (headers, lists, tables)
but is the strictest. ``pymupdf`` ("fitz") is robust on broken xref
tables and CMaps. ``pypdf`` is pure Python and tolerates malformed
streams that even fitz rejects, at the cost of layout fidelity.

A parser is considered "successful" when it returns at least
``min_size`` characters — anything smaller is almost always a parser
failure dressed up as an empty string (e.g. scanned image without
embedded text). We log and fall through to the next stage in that case.

Two distinct failure modes the consumer can branch on:

* :class:`PdfInsufficientTextError` — every parser ran but each one
  returned fewer than ``min_size`` characters. Almost always means the
  PDF is a scan without embedded text; route to OCR.
* :class:`PdfParseError` (base / fallthrough) — at least one parser
  raised an internal exception. Usually a corrupt or unusual PDF
  variant; per-backend detail is in ``exc.per_backend``.
"""

from __future__ import annotations

import io
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Final

log = logging.getLogger(__name__)

DEFAULT_MIN_TEXT_SIZE: Final[int] = 100


class PdfParseError(RuntimeError):
    """All parsers in the fallback chain failed.

    ``per_backend`` maps the parser name (``"pymupdf4llm"``,
    ``"pymupdf"``, ``"pypdf"``) to a short failure description —
    either an exception summary (``"PdfReadError: trailer not found"``)
    or a too-short-text summary (``"produced 12 chars (< 100)"``).
    The client envelope surfaces this dict so agents can see *why*
    each backend failed without having to re-run.
    """

    reason: ClassVar[str] = "all_backends_failed"

    def __init__(self, message: str, *, per_backend: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.per_backend: dict[str, str] = per_backend or {}


class PdfInsufficientTextError(PdfParseError):
    """Every parser ran cleanly but each output was below ``min_size``.

    Distinguishing this from the generic
    :class:`PdfParseError` is the whole point — "scanned PDF, route
    to OCR" is a very different next step from "corrupt PDF, escalate".
    """

    reason: ClassVar[str] = "insufficient_text"


def parse_pdf(data: bytes, *, min_size: int = DEFAULT_MIN_TEXT_SIZE) -> tuple[str, str]:
    """Parse PDF bytes to Markdown, trying pymupdf4llm → pymupdf → pypdf.

    Args:
        data: Raw PDF bytes (e.g. from
            :func:`pdf.fetch.http.download_pdf`).
        min_size: Minimum character count that counts as a successful
            parse. Outputs below this fall through to the next parser.

    Returns:
        ``(markdown_text, parser_used)`` where ``parser_used`` is one of
        ``"pymupdf4llm"``, ``"pymupdf"``, ``"pypdf"``.

    Raises:
        PdfInsufficientTextError: Every parser ran but each produced
            fewer than ``min_size`` characters. Almost always a scan.
        PdfParseError: At least one parser raised internally. Inspect
            ``exc.per_backend`` for the per-parser detail.
    """
    if not data:
        raise PdfParseError("Cannot parse empty PDF bytes")

    per_backend: dict[str, str] = {}

    for name, parser in _PARSERS:
        try:
            text = parser(data)
        except Exception as exc:  # parser-internal failures are expected here
            per_backend[name] = f"{type(exc).__name__}: {exc}"
            log.debug("PDF parser %s failed", name, exc_info=True)
            continue

        if len(text) >= min_size:
            return text, name

        per_backend[name] = f"produced {len(text)} chars (< {min_size})"

    # All backends failed. If every entry is a too-short-text record
    # (no exceptions raised), this is the scanned-PDF case — call it
    # out explicitly so agents can route to OCR instead of generic
    # "parse failed" escalation.
    all_insufficient = bool(per_backend) and all(
        v.startswith("produced ") for v in per_backend.values()
    )
    if all_insufficient:
        raise PdfInsufficientTextError(
            f"All PDF parsers returned text below min_size={min_size}: {per_backend}",
            per_backend=per_backend,
        )

    raise PdfParseError(
        f"All PDF parsers failed: {per_backend}",
        per_backend=per_backend,
    )


def _parse_with_pymupdf4llm(data: bytes) -> str:
    import pymupdf4llm  # type: ignore[import-untyped]

    # pymupdf4llm reads from disk; spool to a temp file rather than
    # holding two copies of the PDF in memory.
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as fp:
        fp.write(data)
        fp.flush()
        return pymupdf4llm.to_markdown(Path(fp.name))


def _parse_with_pymupdf(data: bytes) -> str:
    import pymupdf  # type: ignore[import-untyped]

    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return "\n\n".join(page.get_text() for page in doc)


def _parse_with_pypdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


_PARSERS: Final[tuple[tuple[str, Callable[[bytes], str]], ...]] = (
    ("pymupdf4llm", _parse_with_pymupdf4llm),
    ("pymupdf", _parse_with_pymupdf),
    ("pypdf", _parse_with_pypdf),
)
