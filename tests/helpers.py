"""Shared helpers for the pagewielder tests."""

from pikepdf import Pdf

A4 = (595.0, 842.0)
PLATE = (1000.0, 700.0)


def make_pdf(sizes: list[tuple[float, float]]) -> Pdf:
    """Build a PDF with one blank page per given page size."""
    pdf = Pdf.new()
    for size in sizes:
        pdf.add_blank_page(page_size=size)
    return pdf


def outline_titles(pdf: Pdf) -> list[str]:
    """List the titles of the top-level outline items of a PDF."""
    with pdf.open_outline() as outline:
        return [item.title for item in outline.root]
