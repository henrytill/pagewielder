"""Tests for pagewielder.cli."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pikepdf
from click.testing import CliRunner, Result
from pikepdf import Dictionary, Name, OutlineItem

from pagewielder import cli
from pagewielder.core import Dimensions
from tests.helpers import A4, PLATE, make_pdf, outline_titles, set_page_labels


def _make_input_pdf(path: Path) -> None:
    with make_pdf([A4] * 4) as pdf:
        set_page_labels(pdf, [0, Dictionary(S=Name.D)])
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


class CommandTestCase(unittest.TestCase):
    """Base for the command tests, with an input PDF and a private temporary directory."""

    def setUp(self) -> None:
        """Write a four-page input PDF, and point tempfile at an empty directory of its own."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.input_path = Path(tmp.name) / "input.pdf"
        self.output_path = Path(tmp.name) / "output.pdf"
        _make_input_pdf(self.input_path)

        # A fresh directory for the commands' temporary output files, so a test
        # can see whether one was left behind.
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.temp_dir = Path(temp_dir.name)
        patcher = mock.patch.object(tempfile, "tempdir", temp_dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)


class FilterCommandTest(CommandTestCase):
    """Tests for the filter command."""

    def test_cancelling_leaves_no_temporary_file(self) -> None:
        """Cancelling the prompt with no output path given creates no file."""
        result = CliRunner().invoke(cli.main, ["filter", str(self.input_path)], input="\n")
        self.assertEqual(1, result.exit_code)
        self.assertIn(cli.MSG_NOTHING_SELECTED, result.stderr)
        self.assertEqual([], list(self.temp_dir.iterdir()))

    def test_refuses_to_overwrite_input_before_prompting(self) -> None:
        """Writing back over the input is refused before the user is asked anything."""
        result = CliRunner().invoke(cli.main, ["filter", str(self.input_path), "-o", str(self.input_path)])
        self.assertEqual(2, result.exit_code)
        self.assertIn(cli.MSG_SAME_PATHS, result.stderr)
        self.assertNotIn(cli.PROMPT_AVAILABLE_DIMENSIONS, result.output)


class ExcerptCommandTest(CommandTestCase):
    """Tests for the excerpt command."""

    def _excerpt(self, pages: str, output: Path | None = None) -> Result:
        """Run the excerpt command on the input PDF, writing to the given output or the default one."""
        output = self.output_path if output is None else output
        return CliRunner().invoke(cli.main, ["excerpt", str(self.input_path), pages, "-o", str(output)])

    def test_preserves_outline_for_extracted_pages(self) -> None:
        """The outline survives extraction, pruned to the extracted pages."""
        result = self._excerpt("1:3")
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn(cli.MSG_EXTRACTED_PAGES.format(count=2, pages="1:3", path=self.output_path), result.output)

        with pikepdf.open(self.output_path) as pdf:
            self.assertEqual(2, len(pdf.pages))
            self.assertEqual(["Chapter 2"], outline_titles(pdf))

    def test_extracts_a_single_page(self) -> None:
        """A single index extracts that one page."""
        result = self._excerpt("-1")
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn(cli.MSG_EXTRACTED_PAGE.format(pages="-1", path=self.output_path), result.output)

        with pikepdf.open(self.output_path) as pdf:
            self.assertEqual(["4"], [page.label for page in pdf.pages])

    def test_accepts_a_range_starting_with_a_minus_sign(self) -> None:
        """A range such as -3: is taken as the page range, not as an option."""
        result = self._excerpt("-3:")
        self.assertEqual(0, result.exit_code, result.output)

        with pikepdf.open(self.output_path) as pdf:
            self.assertEqual(["2", "3", "4"], [page.label for page in pdf.pages])

    def test_step_skips_pages(self) -> None:
        """A step extracts every nth page."""
        result = self._excerpt("::2")
        self.assertEqual(0, result.exit_code, result.output)

        with pikepdf.open(self.output_path) as pdf:
            self.assertEqual(["1", "3"], [page.label for page in pdf.pages])

    def test_rejects_invalid_page_range(self) -> None:
        """An invalid page range is reported as a usage error."""
        result = self._excerpt("4")
        self.assertEqual(2, result.exit_code)
        self.assertIn(cli.MSG_INDEX_OUT_OF_RANGE.format(index=4, total=4), result.stderr)
        self.assertFalse(self.output_path.exists())

    def test_invalid_page_range_leaves_no_temporary_file(self) -> None:
        """An invalid page range with no output path given creates no file."""
        result = CliRunner().invoke(cli.main, ["excerpt", str(self.input_path), "4"])
        self.assertEqual(2, result.exit_code)
        self.assertEqual([], list(self.temp_dir.iterdir()))

    def test_refuses_to_overwrite_input(self) -> None:
        """Writing back over the input is refused."""
        result = self._excerpt("1", self.input_path)
        self.assertEqual(2, result.exit_code)
        self.assertIn(cli.MSG_SAME_PATHS, result.stderr)


class ParsePageRangeTest(unittest.TestCase):
    """Tests for parse_page_range."""

    def test_selects_what_python_would(self) -> None:
        """Indices and slices select what they would from a list of the pages, as page numbers."""
        cases = {
            "0": [1],
            "5": [6],
            "-1": [6],
            "-6": [1],
            "0:3": [1, 2, 3],
            "3:": [4, 5, 6],
            ":2": [1, 2],
            ":": [1, 2, 3, 4, 5, 6],
            ":-1": [1, 2, 3, 4, 5],
            "-2:": [5, 6],
            "::2": [1, 3, 5],
            "1::3": [2, 5],
            "1:3:": [2, 3],
            "0:99": [1, 2, 3, 4, 5, 6],
            "-99:2": [1, 2],
        }
        for page_range, expected in cases.items():
            with self.subTest(page_range=page_range):
                self.assertEqual(expected, list(cli.parse_page_range(page_range, 6)))

    def test_rejects_invalid_ranges(self) -> None:
        """Invalid page ranges raise ValueError with the message for their fault."""
        cases = {
            "6": cli.MSG_INDEX_OUT_OF_RANGE.format(index=6, total=6),
            "-7": cli.MSG_INDEX_OUT_OF_RANGE.format(index=-7, total=6),
            "1:2:3:4": cli.MSG_TOO_MANY_COLONS.format(text="1:2:3:4"),
            "::0": cli.MSG_STEP_NOT_POSITIVE.format(step=0),
            "::-1": cli.MSG_STEP_NOT_POSITIVE.format(step=-1),
            "4:1:-1": cli.MSG_STEP_NOT_POSITIVE.format(step=-1),
            "3:3": cli.MSG_NO_PAGES.format(text="3:3"),
            "4:1": cli.MSG_NO_PAGES.format(text="4:1"),
            "6:": cli.MSG_NO_PAGES.format(text="6:"),
        }
        for page_range, message in cases.items():
            with self.subTest(page_range=page_range):
                with self.assertRaises(ValueError) as caught:
                    cli.parse_page_range(page_range, 6)
                self.assertEqual(message, str(caught.exception))

    def test_reports_malformed_page_indices_alike(self) -> None:
        """A malformed page index gets the same message wherever it appears."""
        for page_range in ("x", "x:5", "1:x", "::x"):
            with self.subTest(page_range=page_range):
                with self.assertRaises(ValueError) as caught:
                    cli.parse_page_range(page_range, 6)
                self.assertEqual(cli.MSG_INVALID_INDEX.format(text="x"), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
