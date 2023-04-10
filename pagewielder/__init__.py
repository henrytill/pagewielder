import collections

import pikepdf

Dimensions = tuple[float, float]
Pages = set[int]


def get_page_dimensions(page: pikepdf.Page) -> Dimensions:
    '''Get the dimensions of a page in a PDF file.'''
    rect = pikepdf.Rectangle(page.mediabox)
    return (rect.width, rect.height)


def map_page_dimensions_to_pages(pdf: pikepdf.Pdf) -> dict[Dimensions, Pages]:
    '''Map page dimensions to page numbers.'''
    dimensions_to_pages: dict[Dimensions, Pages] = collections.defaultdict(set)
    for i, page in enumerate(pdf.pages):
        dimensions = get_page_dimensions(page)
        dimensions_to_pages[dimensions].add(i + 1)
    return dimensions_to_pages
