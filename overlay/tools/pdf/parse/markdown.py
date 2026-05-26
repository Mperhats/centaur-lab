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
"""

from __future__ import annotations

import io
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

log = logging.getLogger(__name__)

DEFAULT_MIN_TEXT_SIZE: Final[int] = 100


class PdfParseError(RuntimeError):
    """All parsers in the fallback chain failed to produce usable text."""


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
        PdfParseError: Every parser failed or returned text below
            ``min_size``.
    """
    if not data:
        raise PdfParseError("Cannot parse empty PDF bytes")

    errors: list[str] = []

    for name, parser in _PARSERS:
        try:
            text = parser(data)
        except Exception as exc:  # parser-internal failures are expected here
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            log.debug("PDF parser %s failed", name, exc_info=True)
            continue

        if len(text) >= min_size:
            return text, name

        errors.append(f"{name}: produced {len(text)} chars (< {min_size})")

    raise PdfParseError(
        "All PDF parsers failed or returned insufficient text: " + "; ".join(errors)
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
