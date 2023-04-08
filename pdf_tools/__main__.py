from pathlib import Path

import pdf_tools

pdf_path = Path("test.pdf")
dimensions_to_pages_map = pdf_tools.map_page_dimensions_to_pages(pdf_path)

for dimensions, pages in dimensions_to_pages_map.items():
    width, height = dimensions
    print(f"Dimensions (width x height): {width} x {height}")
    print(f"Pages: {', '.join(str(page) for page in sorted(pages))}\n")
