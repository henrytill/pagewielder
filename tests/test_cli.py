"""Tests for pagewielder.cli."""

import tempfile
import unittest
from pathlib import Path

import pikepdf
from click.testing import CliRunner, Result
from pikepdf import OutlineItem

from pagewielder import cli
from pagewielder.core import Dimensions
from tests.helpers import A4, PLATE, make_pdf, outline_titles


def _make_input_pdf(path: Path) -> None:
    with make_pdf([A4] * 4) as pdf:
        with pdf.open_outline() as outline:
            outline.root.append(OutlineItem("Chapter 1", 0))
            outline.root.append(OutlineItem("Chapter 2", 1))
            outline.root.append(OutlineItem("Chapter 3", 3))
        pdf.save(path)


class SelectDimensionsTest(unittest.TestCase):
    """Tests for select_dimensions."""

    DIMENSIONS_TO_PAGES = {A4: {1, 2}, PLATE: {3}}

    def _select(self, *answers: str) -> tuple[set[Dimensions] | None, str]:
        """Run select_dimensions with the given answers, returning its result and output."""
        with CliRunner().isolation(input="".join(f"{answer}\n" for answer in answers)) as (stdout, _, _):
            selected = cli.select_dimensions(self.DIMENSIONS_TO_PAGES)
            output = stdout.getvalue().decode()
        return selected, output

    def test_selects_by_index(self) -> None:
        """Comma-separated indices select the matching dimensions."""
        selected, output = self._select("0, 1")
        self.assertEqual({A4, PLATE}, selected)
        self.assertNotIn(cli.PROMPT_INVALID_INPUT, output)

    def test_empty_input_cancels(self) -> None:
        """An empty answer cancels the selection."""
        selected, _ = self._select("")
        self.assertIsNone(selected)

    def test_rejects_negative_indices(self) -> None:
        """A negative index is rejected rather than counting from the end."""
        selected, output = self._select("-1", "0")
        self.assertEqual({A4}, selected)
        self.assertEqual(1, output.count(cli.PROMPT_INVALID_INPUT))

    def test_rejects_out_of_range_and_malformed_indices(self) -> None:
        """Indices past the end, and input that is not a number, are rejected."""
        selected, output = self._select("2", "0,x", "1")
        self.assertEqual({PLATE}, selected)
        self.assertEqual(2, output.count(cli.PROMPT_INVALID_INPUT))


class ExcerptCommandTest(unittest.TestCase):
    """Tests for the excerpt command."""

    def setUp(self) -> None:
        """Write a four-page input PDF to a temporary directory."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.input_path = Path(tmp.name) / "input.pdf"
        self.output_path = Path(tmp.name) / "output.pdf"
        _make_input_pdf(self.input_path)

    def _excerpt(self, pages: str, output: Path | None = None) -> Result:
        """Run the excerpt command on the input PDF, writing to the given output or the default one."""
        output = self.output_path if output is None else output
        return CliRunner().invoke(cli.main, ["excerpt", str(self.input_path), pages, "-o", str(output)])

    def test_preserves_outline_for_extracted_pages(self) -> None:
        """The outline survives extraction, pruned to the extracted pages."""
        result = self._excerpt("2:3")
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn(cli.MSG_EXTRACTED_PAGES.format(count=2, start=2, end=3, path=self.output_path), result.output)

        with pikepdf.open(self.output_path) as pdf:
            self.assertEqual(2, len(pdf.pages))
            self.assertEqual(["Chapter 2"], outline_titles(pdf))

    def test_rejects_invalid_page_range(self) -> None:
        """An invalid page range is reported as a usage error."""
        result = self._excerpt("5")
        self.assertEqual(2, result.exit_code)
        self.assertIn(cli.MSG_PAGE_OUT_OF_RANGE.format(page=5, total=4), result.stderr)
        self.assertFalse(self.output_path.exists())

    def test_refuses_to_overwrite_input(self) -> None:
        """Writing back over the input is refused."""
        result = self._excerpt("1", self.input_path)
        self.assertEqual(2, result.exit_code)
        self.assertIn(cli.MSG_SAME_PATHS, result.stderr)


class ParsePageRangeTest(unittest.TestCase):
    """Tests for parse_page_range."""

    def test_parses_ranges(self) -> None:
        """Single pages, ranges, and open-ended ranges are parsed."""
        self.assertEqual((7, 7), cli.parse_page_range("7", 10))
        self.assertEqual((1, 5), cli.parse_page_range("1:5", 10))
        self.assertEqual((3, 10), cli.parse_page_range("3:", 10))
        self.assertEqual((1, 10), cli.parse_page_range(":10", 10))

    def test_rejects_invalid_ranges(self) -> None:
        """Out-of-range inputs raise ValueError with the message for their fault."""
        cases = {
            "0": cli.MSG_PAGE_OUT_OF_RANGE.format(page=0, total=10),
            "11": cli.MSG_PAGE_OUT_OF_RANGE.format(page=11, total=10),
            "0:5": cli.MSG_START_TOO_LOW.format(start=0),
            "1:11": cli.MSG_END_TOO_HIGH.format(end=11, total=10),
            "5:1": cli.MSG_START_AFTER_END.format(start=5, end=1),
        }
        for page_range, message in cases.items():
            with self.subTest(page_range=page_range):
                with self.assertRaises(ValueError) as caught:
                    cli.parse_page_range(page_range, 10)
                self.assertEqual(message, str(caught.exception))

    def test_reports_malformed_page_numbers_alike(self) -> None:
        """A malformed page number gets the same message wherever it appears."""
        for page_range in ("x", "x:5", "1:x"):
            with self.subTest(page_range=page_range):
                with self.assertRaises(ValueError) as caught:
                    cli.parse_page_range(page_range, 10)
                self.assertEqual(cli.MSG_INVALID_PAGE.format(text="x"), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
