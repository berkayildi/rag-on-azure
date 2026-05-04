"""Shared unit-test fixtures.

``make_pdf`` builds tiny in-memory PDFs from page-text strings using ``fpdf2``.
Used by ``test_fetch.py`` PDF-extraction tests; preferred over hand-written
byte literals because it doesn't lean on pypdf parser leniency that may drift
across versions.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fpdf import FPDF


@pytest.fixture
def make_pdf() -> Callable[[list[str]], bytes]:
    def _build(pages: list[str]) -> bytes:
        pdf = FPDF()
        pdf.set_font("Helvetica", size=12)
        for text in pages:
            pdf.add_page()
            pdf.multi_cell(0, 10, text)
        return bytes(pdf.output())

    return _build
