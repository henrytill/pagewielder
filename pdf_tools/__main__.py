import argparse
import tempfile
from pathlib import Path

import pikepdf

import pdf_tools
from pdf_tools import Dimensions, Pages


def user_select_multiple_dimensions(
    dimensions_to_pages_map: dict[Dimensions, Pages]
) -> set[Dimensions]:
    """
    Prompt the user to one or more dimensions from a list of dimensions and the
    corresponding number of pages.
    """
    dimensions_list = list(dimensions_to_pages_map.keys())

    print("Select page dimensions to filter (separated by commas):")
    for i, dimensions in enumerate(dimensions_list, start=1):
        width, height = dimensions
        num_pages = len(dimensions_to_pages_map[dimensions])
        print(
            "{0}. Dimensions (width x height): {1:8.2f} x {2:8.2f} | Number of pages: {3}".format(
                i, width, height, num_pages
            )
        )

    selected_indices = input(
        "Enter the numbers of the dimensions you want to filter (e.g., '1, 3'): "
    )
    selected_indices = [int(idx.strip()) - 1 for idx in selected_indices.split(",")]
    return {dimensions_list[idx] for idx in selected_indices}


def main():
    parser = argparse.ArgumentParser(
        description="Filter PDF pages based on dimensions."
    )
    parser.add_argument("input", type=Path, help="Path to the input PDF file")
    parser.add_argument(
        "-o", "--output", type=Path, help="Path to the output PDF file (optional)"
    )

    args = parser.parse_args()
    input_path = args.input

    if args.output:
        output_path = args.output
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpfile:
            output_path = Path(tmpfile.name)

    dimensions_to_pages_map = pdf_tools.map_page_dimensions_to_pages(input_path)
    selected_dimensions_set = user_select_multiple_dimensions(dimensions_to_pages_map)

    selected_pages = set()
    for dimensions in selected_dimensions_set:
        selected_pages.update(dimensions_to_pages_map[dimensions])

    with pikepdf.open(input_path) as pdf, pikepdf.Pdf.new() as output_pdf:
        for i, page in enumerate(pdf.pages, start=1):
            if i not in selected_pages:
                output_pdf.pages.append(page)
        output_pdf.save(output_path)

    print(f"Filtered PDF saved as '{output_path}'")


if __name__ == "__main__":
    main()
