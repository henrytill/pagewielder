"""Command-line interface for pagewielder."""
import argparse
import tempfile
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pikepdf

from . import core
from .core import Dimensions, Pages


# pylint: disable=too-few-public-methods
@dataclass(frozen=True)
class Prompt:
    """Prompt strings used in the command-line interface."""

    AVAILABLE_DIMENSIONS = "Available dimensions (width x height) and number of pages:"
    SELECT_DIMENSIONS = "Select page sets to remove by index (comma-separated) or press Enter to cancel: "
    INVALID_INPUT = "Invalid input. Please enter valid indices separated by commas.\n"


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


def filter_command(args: Namespace) -> None:
    """Filter a PDF file based on page dimensions.

    Args:
        args: Command-line arguments.
    """
    input_path: Path = args.input
    output_path: Optional[Path] = None

    if args.output is not None:
        output_path = args.output
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpfile:
            output_path = Path(tmpfile.name)

    if input_path == output_path:
        print("Input and output paths must be different.")
        return

    with pikepdf.open(input_path) as input_pdf:
        dimensions_to_pages = core.map_dimensions_to_pages(input_pdf)
        maybe_selected_dimensions = select_dimensions(dimensions_to_pages)

        if maybe_selected_dimensions is None:
            print("No page sets selected. No output file created.")
            return

        selected_pages: Pages = set()
        for page_dimensions in maybe_selected_dimensions:
            selected_pages.update(dimensions_to_pages[page_dimensions])

        with pikepdf.Pdf.new() as output_pdf:
            for i, page in enumerate(input_pdf.pages, start=1):
                if i not in selected_pages:
                    output_pdf.pages.append(page)
            output_pdf.save(output_path)

    print(f"Filtered PDF saved as {output_path}")


def main() -> int:
    """The main entry point for the command-line interface.

    Returns:
        An exit code.
    """
    parser = argparse.ArgumentParser(description="pagewielder")
    subparsers = parser.add_subparsers(help="Commands")

    filter_parser = subparsers.add_parser("filter", help="Filter PDF pages based on dimensions")
    filter_parser.add_argument("input", type=Path, help="Path to the input PDF file")
    filter_parser.add_argument("-o", "--output", type=Path, help="Path to the output PDF file (optional)")
    filter_parser.set_defaults(func=filter_command)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return 2

    ret: int = args.func(args)

    return ret
