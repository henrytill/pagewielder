"""Tests for pagewielder.cli."""

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import pikepdf
from pikepdf import OutlineItem

from pagewielder import cli
from tests.helpers import A4, make_pdf, outline_titles


def _make_input_pdf(path: Path) -> None:
    with make_pdf([A4] * 4) as pdf:
        with pdf.open_outline() as outline:
            outline.root.append(OutlineItem("Chapter 1", 0))
            outline.root.append(OutlineItem("Chapter 2", 1))
            outline.root.append(OutlineItem("Chapter 3", 3))
        pdf.save(path)


class ExcerptCommandTest(unittest.TestCase):
    """Tests for excerpt_command."""

    def test_preserves_outline_for_extracted_pages(self) -> None:
        """The outline survives extraction, pruned to the extracted pages."""
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.pdf"
            output_path = Path(tmp) / "output.pdf"
            _make_input_pdf(input_path)

            args = Namespace(input=input_path, pages="2:3", output=output_path)
            self.assertEqual(0, cli.excerpt_command(args))

            with pikepdf.open(output_path) as pdf:
                self.assertEqual(2, len(pdf.pages))
                self.assertEqual(["Chapter 2"], outline_titles(pdf))


class ParsePageRangeTest(unittest.TestCase):
    """Tests for parse_page_range."""

    def test_parses_ranges(self) -> None:
        """Single pages, ranges, and open-ended ranges are parsed."""
        self.assertEqual((7, 7), cli.parse_page_range("7", 10))
        self.assertEqual((1, 5), cli.parse_page_range("1:5", 10))
        self.assertEqual((3, 10), cli.parse_page_range("3:", 10))
        self.assertEqual((1, 10), cli.parse_page_range(":10", 10))

    def test_rejects_invalid_ranges(self) -> None:
        """Out-of-range and malformed inputs raise ValueError."""
        for page_range in ("0", "11", "5:1", "1:11", "abc"):
            with self.assertRaises(ValueError):
                cli.parse_page_range(page_range, 10)


if __name__ == "__main__":
    unittest.main()
