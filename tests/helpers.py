"""Shared helpers for the pagewielder tests."""

from collections.abc import Sequence

from pikepdf import Array, Dictionary, NumberTree, Pdf

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


def set_page_labels(pdf: Pdf, nums: Sequence[int | Dictionary]) -> None:
    """Give a PDF a /PageLabels number tree from a flat /Nums list."""
    pdf.Root.PageLabels = pdf.make_indirect(Dictionary(Nums=Array(nums)))


def page_label_ranges(pdf: Pdf) -> list[tuple[int, dict[str, str]]]:
    """List a PDF's /PageLabels ranges as plain Python values."""
    return [
        (index, {str(key): str(value) for key, value in entry.items()})
        for index, entry in NumberTree(pdf.Root.PageLabels).items()
    ]
