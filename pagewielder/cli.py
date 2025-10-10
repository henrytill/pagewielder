"""Command-line interface for pagewielder."""

import argparse
import sys
import tempfile
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import pikepdf

from . import __version__, core
from .core import Dimensions, Pages


# pylint: disable=too-few-public-methods
@dataclass(frozen=True, init=False)
class Prompt:
    """Prompt strings used in the command-line interface."""

    AVAILABLE_DIMENSIONS = "Available dimensions (width x height) and number of pages:"
    SELECT_DIMENSIONS = "Select page sets to remove by index (comma-separated) or press Enter to cancel: "
    INVALID_INPUT = "Invalid input. Please enter valid indices separated by commas.\n"


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
        try:
            page = int(parts[0])
            if page < 1 or page > total_pages:
                raise ValueError(f"Page {page} is out of range (1-{total_pages})")
            return (page, page)
        except ValueError as e:
            if "out of range" in str(e):
                raise
            raise ValueError(f"Invalid page number: {parts[0]}") from e

    if len(parts) == 2:
        # Range
        start_str, end_str = parts

        # Default values
        start = 1 if not start_str else int(start_str)
        end = total_pages if not end_str else int(end_str)

        if start < 1:
            raise ValueError(f"Start page {start} must be >= 1")
        if end > total_pages:
            raise ValueError(f"End page {end} exceeds total pages ({total_pages})")
        if start > end:
            raise ValueError(f"Start page {start} must be <= end page {end}")

        return (start, end)

    raise ValueError(f"Invalid page range format: {page_range}")


def select_dimensions(dimensions_to_pages: dict[Dimensions, Pages]) -> Optional[set[Dimensions]]:
    """Prompt the user to one or more dimensions from a list of dimensions and
    the corresponding number of pages.

    Args:
        dimensions_to_pages: A dictionary mapping dimensions to the set of
            pages with those dimensions.

    Returns:
        A set of dimensions to remove or None if the user cancels.
    """
    dimensions_list = list(dimensions_to_pages.keys())

    print(Prompt.AVAILABLE_DIMENSIONS)

    for i, dimensions in enumerate(dimensions_list):
        width, height = dimensions
        num_pages = len(dimensions_to_pages[dimensions])
        print(f"{i}: {width:.2f} x {height:.2f} ({num_pages} pages)")

    while True:
        user_input = input(Prompt.SELECT_DIMENSIONS)
        if not user_input:
            return None
        selected_dimensions = user_input.split(",")
        try:
            return {dimensions_list[int(index)] for index in selected_dimensions}
        except (ValueError, IndexError):
            print(Prompt.INVALID_INPUT)


def filter_command(args: Namespace) -> int:
    """Filter a PDF file based on page dimensions.

    Args:
        args: Command-line arguments.

    Returns:
        An exit code.
    """
    input_path: Path = args.input
    output_path: Optional[Path] = None

    if args.output is not None:
        output_path = args.output
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpfile:
            output_path = Path(tmpfile.name)

    if input_path == output_path:
        print("Input and output paths must be different.", file=sys.stderr)
        return 1

    with pikepdf.open(input_path) as input_pdf:
        dimensions_to_pages = core.map_dimensions_to_pages(input_pdf)
        maybe_selected_dimensions = select_dimensions(dimensions_to_pages)

        if maybe_selected_dimensions is None:
            print("No page sets selected. No output file created.", file=sys.stderr)
            return 1

        selected_pages: Pages = set()
        for page_dimensions in maybe_selected_dimensions:
            selected_pages.update(dimensions_to_pages[page_dimensions])

        with pikepdf.Pdf.new() as output_pdf:
            for i, page in enumerate(input_pdf.pages, start=1):
                if i not in selected_pages:
                    output_pdf.pages.append(page)
            output_pdf.save(output_path)

    print(f"Filtered PDF saved as {output_path}")

    return 0


def excerpt_command(args: Namespace) -> int:
    """Extract a range of pages from a PDF file.

    Args:
        args: Command-line arguments.

    Returns:
        An exit code.
    """
    input_path: Path = args.input
    output_path: Optional[Path] = None

    if args.output is not None:
        output_path = args.output
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpfile:
            output_path = Path(tmpfile.name)

    if input_path == output_path:
        print("Input and output paths must be different.", file=sys.stderr)
        return 1

    with pikepdf.open(input_path) as input_pdf:
        total_pages = len(input_pdf.pages)

        try:
            start_page, end_page = parse_page_range(args.pages, total_pages)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        with pikepdf.Pdf.new() as output_pdf:
            # pikepdf uses 0-based indexing internally
            for i in range(start_page - 1, end_page):
                output_pdf.pages.append(input_pdf.pages[i])
            output_pdf.save(output_path)

    page_count = end_page - start_page + 1
    print(f"Extracted {page_count} page{'s' if page_count != 1 else ''} ({start_page}:{end_page}) to {output_path}")

    return 0


def main(args: Sequence[str] = sys.argv[1:]) -> int:
    """The main entry point for the command-line interface.

    Returns:
        An exit code.
    """
    parser = argparse.ArgumentParser(description="pagewielder")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(help="Commands")

    filter_parser = subparsers.add_parser("filter", help="Filter PDF pages based on dimensions")
    filter_parser.add_argument("input", type=Path, help="Path to the input PDF file")
    filter_parser.add_argument("-o", "--output", type=Path, help="Path to the output PDF file (optional)")
    filter_parser.set_defaults(func=filter_command)

    excerpt_parser = subparsers.add_parser("excerpt", help="Extract a range of pages from a PDF")
    excerpt_parser.add_argument("input", type=Path, help="Path to the input PDF file")
    excerpt_parser.add_argument("pages", help="Page range to extract (e.g., 1:5, 3:, :10, 7)")
    excerpt_parser.add_argument("-o", "--output", type=Path, help="Path to the output PDF file (optional)")
    excerpt_parser.set_defaults(func=excerpt_command)

    parsed = parser.parse_args(args)

    if not hasattr(parsed, "func"):
        parser.print_help()
        return 2

    if not callable(parsed.func):
        raise TypeError("Expected callable")

    ret = parsed.func(parsed)
    if not isinstance(ret, int):
        raise TypeError("Expected int")

    return ret
