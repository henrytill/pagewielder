import argparse
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Optional

import pikepdf

import pdf_tools
from pdf_tools import Dimensions, Pages


def user_select_multiple_dimensions(
    dimensions_to_pages_map: dict[Dimensions, Pages]
) -> Optional[set[Dimensions]]:
    """
    Prompt the user to one or more dimensions from a list of dimensions and the
    corresponding number of pages.
    """
    dimensions_list = list(dimensions_to_pages_map.keys())
    print("Available dimensions (width x height) and number of pages:")
    for i, dimensions in enumerate(dimensions_list):
        width, height = dimensions
        num_pages = len(dimensions_to_pages_map[dimensions])
        print(f"{i}: {width:.2f} x {height:.2f} ({num_pages} pages)")

    while True:
        user_input = input(
            "Select page sets to remove by index (comma-separated) or press Enter to cancel: "
        )
        if not user_input:
            return None
        selected_dimensions = user_input.split(",")
        try:
            selected_dimensions_set = {
                dimensions_list[int(index)] for index in selected_dimensions
            }
            return selected_dimensions_set
        except (ValueError, IndexError):
            print("Invalid input. Please enter valid indices separated by commas.\n")


def filter_command(args: Namespace):
    input_path: Path = args.input

    if args.output:
        output_path: Path = args.output
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpfile:
            output_path = Path(tmpfile.name)

    dimensions_to_pages_map = pdf_tools.map_page_dimensions_to_pages(input_path)
    selected_dimensions_set = user_select_multiple_dimensions(dimensions_to_pages_map)

    if selected_dimensions_set is None:
        print("No page sets selected. No output file created.")
        return

    selected_pages: Pages = set()
    for dimensions in selected_dimensions_set:
        selected_pages.update(dimensions_to_pages_map[dimensions])

    with pikepdf.open(input_path) as input_pdf, pikepdf.Pdf.new() as output_pdf:
        for i, page in enumerate(input_pdf.pages, start=1):
            if i not in selected_pages:
                output_pdf.pages.append(page)
        output_pdf.save(output_path)

    print(f"Filtered PDF saved as '{output_path}'")


def main():
    parser = argparse.ArgumentParser(description="PDF Tools")
    subparsers = parser.add_subparsers(help="Commands")

    filter_parser = subparsers.add_parser(
        "filter", help="Filter PDF pages based on dimensions"
    )
    filter_parser.add_argument("input", type=Path, help="Path to the input PDF file")
    filter_parser.add_argument(
        "-o", "--output", type=Path, help="Path to the output PDF file (optional)"
    )
    filter_parser.set_defaults(func=filter_command)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
