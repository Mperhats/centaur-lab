"""Unit tests for the pure helpers in ``pdf.utils``.

Only the two new content-byte helpers are covered here —
``force_pdf_mime`` and ``derive_filename_from_url`` predate this
test module and intentionally remain untested for now (out of scope
for the foundation refactor).
"""

from __future__ import annotations

import hashlib

import pytest

from pdf.utils import compute_pdf_sha256, truncate_utf8


def test_compute_pdf_sha256_matches_hashlib_reference() -> None:
    """Locks the digest byte form: hex SHA-256 over the exact input bytes."""
    data = b"%PDF-1.4 fake content"
    expected = hashlib.sha256(data).hexdigest()

    assert compute_pdf_sha256(data) == expected
    assert len(compute_pdf_sha256(data)) == 64


def test_compute_pdf_sha256_is_deterministic() -> None:
    data = b"\x00\x01\x02\x03\xff" * 1024

    assert compute_pdf_sha256(data) == compute_pdf_sha256(data)


def test_compute_pdf_sha256_distinguishes_different_payloads() -> None:
    assert compute_pdf_sha256(b"a") != compute_pdf_sha256(b"b")


def test_compute_pdf_sha256_handles_empty_bytes() -> None:
    """Empty input is valid (an empty body still has a stable hash)."""
    assert compute_pdf_sha256(b"") == hashlib.sha256(b"").hexdigest()


def test_truncate_utf8_returns_input_when_under_cap() -> None:
    text = "hello"

    truncated, was_truncated = truncate_utf8(text, max_bytes=100)

    assert truncated == "hello"
    assert was_truncated is False


def test_truncate_utf8_returns_input_when_exactly_at_cap() -> None:
    """Equality is *not* truncation — the body fits, so emit it verbatim."""
    text = "abcde"
    cap = len(text.encode("utf-8"))

    truncated, was_truncated = truncate_utf8(text, max_bytes=cap)

    assert truncated == text
    assert was_truncated is False


def test_truncate_utf8_truncates_ascii_input_above_cap() -> None:
    text = "abcdefghij"

    truncated, was_truncated = truncate_utf8(text, max_bytes=4)

    assert truncated == "abcd"
    assert was_truncated is True


def test_truncate_utf8_does_not_split_multibyte_codepoint() -> None:
    """A 3-byte CJK codepoint must not be cut mid-byte; cap counts bytes."""
    text = "日本語abc"
    encoded = text.encode("utf-8")
    assert len(encoded) == 12  # 3*3 + 3 ASCII

    truncated, was_truncated = truncate_utf8(text, max_bytes=4)

    assert was_truncated is True
    assert truncated == "日"
    assert len(truncated.encode("utf-8")) <= 4


def test_truncate_utf8_with_zero_cap_returns_empty_string() -> None:
    text = "hello"

    truncated, was_truncated = truncate_utf8(text, max_bytes=0)

    assert truncated == ""
    assert was_truncated is True


@pytest.mark.parametrize("input_text", ["", "a", "日本語" * 100, "x" * 10000])
def test_truncate_utf8_output_never_exceeds_cap(input_text: str) -> None:
    cap = 50

    truncated, _ = truncate_utf8(input_text, max_bytes=cap)

    assert len(truncated.encode("utf-8")) <= cap
