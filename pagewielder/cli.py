"""Command-line interface for pagewielder."""

import tempfile
from pathlib import Path

import click
import pikepdf

from . import __version__, core
from .core import Dimensions, Pages

# Every user-facing message lives here, so that tests can match on it rather
# than on its text.  Click's own strings, help and version, stay with the
# commands they configure.

# Everything select_dimensions() prints.  click.prompt() adds the ": ".
PROMPT_AVAILABLE_DIMENSIONS = "Available dimensions (width x height) and number of pages:"
PROMPT_DIMENSIONS_CHOICE = "{index}: {width:.2f} x {height:.2f} ({count} pages)"
PROMPT_SELECT_DIMENSIONS = "Select page sets to remove by index (comma-separated) or press Enter to cancel"
PROMPT_INVALID_INPUT = "Invalid input. Please enter valid indices separated by commas.\n"

# Page range errors, raised by parse_page_range().
MSG_INVALID_PAGE = "Invalid page number: {text}"
MSG_PAGE_OUT_OF_RANGE = "Page {page} is out of range (1-{total})"
MSG_START_TOO_LOW = "Start page {start} must be >= 1"
MSG_END_TOO_HIGH = "End page {end} exceeds total pages ({total})"
MSG_START_AFTER_END = "Start page {start} must be <= end page {end}"

# Command output.
MSG_SAME_PATHS = "Input and output paths must be different."
MSG_NOTHING_SELECTED = "No page sets selected. No output file created."
MSG_FILTERED = "Filtered PDF saved as {path}"
MSG_EXTRACTED_PAGE = "Extracted 1 page ({start}:{end}) to {path}"
MSG_EXTRACTED_PAGES = "Extracted {count} pages ({start}:{end}) to {path}"


def _parse_page_number(text: str) -> int:
    """Parse one page number out of a page range.

    Args:
        text: The page number as written on the command line.

    Returns:
        The page number.

    Raises:
        ValueError: If the text is not a whole number.
    """
    try:
        return int(text)
    except ValueError as e:
        raise ValueError(MSG_INVALID_PAGE.format(text=text)) from e


def parse_page_range(page_range: str, total_pages: int) -> tuple[int, int]:
    """Parse a page range string and return start and end page numbers.

    Args:
        page_range: A string representing a page range (e.g., "1:5", "3:", ":10")
        total_pages: The total number of pages in the document

    Returns:
        A tuple of (start_page, end_page) using 1-based indexing

    Raises:
        ValueError: If the page range is invalid
    """
    parts = page_range.split(":", 1)

    if len(parts) == 1:
        # Single page
        page = _parse_page_number(parts[0])
        if page < 1 or page > total_pages:
            raise ValueError(MSG_PAGE_OUT_OF_RANGE.format(page=page, total=total_pages))
        return (page, page)

    # Range.  A split with maxsplit=1 yields no more than two parts.
    start_str, end_str = parts

    start = 1 if not start_str else _parse_page_number(start_str)
    end = total_pages if not end_str else _parse_page_number(end_str)

    if start < 1:
        raise ValueError(MSG_START_TOO_LOW.format(start=start))
    if end > total_pages:
        raise ValueError(MSG_END_TOO_HIGH.format(end=end, total=total_pages))
    if start > end:
        raise ValueError(MSG_START_AFTER_END.format(start=start, end=end))

    return (start, end)


def select_dimensions(dimensions_to_pages: dict[Dimensions, Pages]) -> set[Dimensions] | None:
    """Prompt the user to select one or more dimensions to remove.

    Each dimension is listed with the number of pages that have it.

    Args:
        dimensions_to_pages: A dictionary mapping dimensions to the set of
            pages with those dimensions.

    Returns:
        A set of dimensions to remove or None if the user cancels.
    """
    # Keyed by the label shown for each set, so that only those labels select
    # anything: indexing a list would also accept a negative index.
    choices = dict(enumerate(dimensions_to_pages))

    click.echo(PROMPT_AVAILABLE_DIMENSIONS)

    for i, dimensions in choices.items():
        width, height = dimensions
        num_pages = len(dimensions_to_pages[dimensions])
        click.echo(PROMPT_DIMENSIONS_CHOICE.format(index=i, width=width, height=height, count=num_pages))

    while True:
        # Without a default, click.prompt() would ask again on an empty answer
        # rather than let it cancel.
        user_input: str = click.prompt(PROMPT_SELECT_DIMENSIONS, default="", show_default=False)
        if not user_input:
            return None
        try:
            return {choices[int(index)] for index in user_input.split(",")}
        except (ValueError, KeyError):
            click.echo(PROMPT_INVALID_INPUT)


def _resolve_output_path(input_path: Path, output: Path | None) -> Path:
    """Choose the path to write to.

    Args:
        input_path: The path the command reads from.
        output: The path requested on the command line, or None to write to a
            temporary file.

    Returns:
        The path to write to.

    Raises:
        click.UsageError: If the path is the one being read from.
    """
    if output is None:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpfile:
            output = Path(tmpfile.name)

    if input_path == output:
        raise click.UsageError(MSG_SAME_PATHS)

    return output


_INPUT = click.argument("input_path", metavar="INPUT", type=click.Path(exists=True, dir_okay=False, path_type=Path))
_OUTPUT = click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to the output PDF file.  Defaults to a fresh temporary file.",
)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="pagewielder", message="%(prog)s %(version)s")
def main() -> None:
    """Manipulate PDFs."""


@main.command(
    "filter",
    short_help="Remove pages by their dimensions.",
    help="Remove pages from INPUT by their dimensions, chosen interactively.",
)
@_INPUT
@_OUTPUT
def filter_command(input_path: Path, output: Path | None) -> None:
    """Filter a PDF file based on page dimensions.

    Args:
        input_path: The PDF to read.
        output: The PDF to write, or None to write to a temporary file.

    Raises:
        click.exceptions.Exit: If no page sets are selected.
    """
    output_path = _resolve_output_path(input_path, output)

    with pikepdf.open(input_path) as input_pdf:
        dimensions_to_pages = core.map_dimensions_to_pages(input_pdf)
        maybe_selected_dimensions = select_dimensions(dimensions_to_pages)

        if maybe_selected_dimensions is None:
            click.echo(MSG_NOTHING_SELECTED, err=True)
            raise click.exceptions.Exit(1)

        selected_pages: Pages = set()
        for page_dimensions in maybe_selected_dimensions:
            selected_pages.update(dimensions_to_pages[page_dimensions])

        core.remove_pages(input_pdf, selected_pages)
        input_pdf.save(output_path)

    click.echo(MSG_FILTERED.format(path=output_path))


@main.command(
    "excerpt",
    short_help="Extract a range of pages.",
    help="Extract the PAGES of INPUT, a range such as 1:5, 3:, :10 or 7.  "
    "Pages are numbered from 1 and both ends are included, so 1:5 is the first five pages.",
)
@_INPUT
@click.argument("pages")
@_OUTPUT
def excerpt_command(input_path: Path, pages: str, output: Path | None) -> None:
    """Extract a range of pages from a PDF file.

    Args:
        input_path: The PDF to read.
        pages: The page range to keep.
        output: The PDF to write, or None to write to a temporary file.

    Raises:
        click.BadParameter: If the page range is invalid.
    """
    with pikepdf.open(input_path) as input_pdf:
        total_pages = len(input_pdf.pages)

        try:
            start_page, end_page = parse_page_range(pages, total_pages)
        except ValueError as e:
            raise click.BadParameter(str(e), param_hint="PAGES") from e

        # Only once the range is known to be good, so that a bad one leaves no
        # empty temporary file behind.
        output_path = _resolve_output_path(input_path, output)

        outside_range = set(range(1, start_page)) | set(range(end_page + 1, total_pages + 1))
        core.remove_pages(input_pdf, outside_range)
        input_pdf.save(output_path)

    page_count = end_page - start_page + 1
    message = MSG_EXTRACTED_PAGE if page_count == 1 else MSG_EXTRACTED_PAGES
    click.echo(message.format(count=page_count, start=start_page, end=end_page, path=output_path))
