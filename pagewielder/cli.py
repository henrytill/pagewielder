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
MSG_INVALID_INDEX = "Invalid page index: {text}"
MSG_INDEX_OUT_OF_RANGE = "Page index {index} is out of range for a document of {total} pages"
MSG_TOO_MANY_COLONS = "Invalid page range: {text} (a slice has at most two colons)"
MSG_STEP_NOT_POSITIVE = "Slice step must be positive, not {step}"
MSG_NO_PAGES = "Page range {text} selects no pages"

# Command output.
MSG_SAME_PATHS = "Input and output paths must be different."
MSG_NOTHING_SELECTED = "No page sets selected. No output file created."
MSG_FILTERED = "Filtered PDF saved as {path}"
MSG_EXTRACTED_PAGE = "Extracted 1 page ({pages}) to {path}"
MSG_EXTRACTED_PAGES = "Extracted {count} pages ({pages}) to {path}"


def _parse_page_index(text: str) -> int:
    """Parse one page index out of a page range.

    Args:
        text: The page index as written on the command line.

    Returns:
        The page index.

    Raises:
        ValueError: If the text is not a whole number.
    """
    try:
        return int(text)
    except ValueError as e:
        raise ValueError(MSG_INVALID_INDEX.format(text=text)) from e


def parse_page_range(page_range: str, total_pages: int) -> range:
    """Parse a page range written in Python's slice syntax.

    The range selects what indexing or slicing a list of the document's pages
    would, with two exceptions: the step must be positive, since a negative
    one would ask for the pages to be reordered rather than only removed, and
    a slice selecting no pages is an error.

    Args:
        page_range: A string representing a page range (e.g., "0:5", "-3:", "::2", "7")
        total_pages: The total number of pages in the document

    Returns:
        The selected pages, numbered from 1 as core numbers them, in document
        order.

    Raises:
        ValueError: If the page range is malformed, has a step that is not
            positive, selects no pages, or is a single index naming no page.
    """
    # Indexing and slicing a range follow the rules for a list, negative
    # indices and clamping included.  Using the page numbers rather than
    # range(total_pages) gives back pages numbered from 1, ready for core.
    all_pages = range(1, total_pages + 1)

    match page_range.split(":"):
        case [index_text]:
            index = _parse_page_index(index_text)
            try:
                number = all_pages[index]
            except IndexError as e:
                raise ValueError(MSG_INDEX_OUT_OF_RANGE.format(index=index, total=total_pages)) from e
            return range(number, number + 1)
        case [start_text, stop_text]:
            step_text = ""
        case [start_text, stop_text, step_text]:
            pass
        case _:
            raise ValueError(MSG_TOO_MANY_COLONS.format(text=page_range))

    start, stop, step = (_parse_page_index(text) if text else None for text in (start_text, stop_text, step_text))
    if step is not None and step < 1:
        raise ValueError(MSG_STEP_NOT_POSITIVE.format(step=step))

    pages = all_pages[start:stop:step]
    if not pages:
        raise ValueError(MSG_NO_PAGES.format(text=page_range))

    return pages


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


def _check_output_path(input_path: Path, output: Path | None) -> None:
    """Refuse an output path that is the one being read from.

    Args:
        input_path: The path the command reads from.
        output: The path requested on the command line, or None to write to a
            temporary file.

    Raises:
        click.UsageError: If the path is the one being read from.
    """
    if output == input_path:
        raise click.UsageError(MSG_SAME_PATHS)


def _output_path(output: Path | None) -> Path:
    """Choose the path to write to, allocating a temporary file if none was given.

    Called only once there is something to write, so that a command failing
    or cancelled before then leaves no empty temporary file behind.

    Args:
        output: The path requested on the command line, or None to write to a
            temporary file.

    Returns:
        The path to write to.
    """
    if output is not None:
        return output
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpfile:
        return Path(tmpfile.name)


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
    _check_output_path(input_path, output)

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
        output_path = _output_path(output)
        input_pdf.save(output_path)

    click.echo(MSG_FILTERED.format(path=output_path))


@main.command(
    "excerpt",
    # Passes a range starting with a minus sign, such as -1 or -3:, through as
    # PAGES rather than rejecting it as an unknown option.  Click has no way to
    # do that for one argument only, so a mistyped option reaches PAGES too, and
    # is reported as a bad page range or an extra argument instead.
    context_settings={"ignore_unknown_options": True},
    short_help="Extract a range of pages.",
    help="Extract the PAGES of INPUT, chosen with Python's slice syntax.  Pages are indexed from 0, a "
    "negative index counts from the end, and a slice excludes its stop: 0:5 is the first five pages, 7 the "
    "eighth, -3: the last three, and ::2 every other page.  The step must be positive.",
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
    _check_output_path(input_path, output)

    with pikepdf.open(input_path) as input_pdf:
        total_pages = len(input_pdf.pages)

        try:
            selected = parse_page_range(pages, total_pages)
        except ValueError as e:
            raise click.BadParameter(str(e), param_hint="PAGES") from e

        core.remove_pages(input_pdf, {number for number in range(1, total_pages + 1) if number not in selected})
        output_path = _output_path(output)
        input_pdf.save(output_path)

    message = MSG_EXTRACTED_PAGE if len(selected) == 1 else MSG_EXTRACTED_PAGES
    click.echo(message.format(count=len(selected), pages=pages, path=output_path))
