"""Core functionality for pagewielder."""

import collections

from pikepdf import OutlineItem, Page, Pdf, Rectangle

Dimensions = tuple[float, float]
Pages = set[int]


def _get_dimensions(page: Page) -> Dimensions:
    """Get the dimensions of a page in a PDF file.

    Args:
        page: A page in a PDF file.

    Returns:
        The dimensions of the page.
    """
    rect = Rectangle(page.mediabox)
    return (rect.width, rect.height)


def remap_outline(
    items: list[OutlineItem],
    input_page_objgens: dict[tuple[int, int], int],
    old_to_new: dict[int, int],
) -> list[OutlineItem]:
    """Recursively filter and remap outline items after page removal.

    Items whose destination page was removed are dropped; their children are
    promoted to the parent level. Items whose destination page was kept have
    their destinations updated to the new 0-based page index.

    Args:
        items: Outline items to process.
        input_page_objgens: Mapping from (obj, gen) tuple of each input page to
            its 0-based index in the input PDF.
        old_to_new: Mapping from 0-based old page index to 0-based new index
            for pages that were kept.

    Returns:
        Filtered and remapped list of outline items.
    """
    result = []
    for item in items:
        new_children = remap_outline(item.children, input_page_objgens, old_to_new)
        dest = item.destination
        if isinstance(dest, list) and dest:
            try:
                old_idx = input_page_objgens.get(dest[0].objgen)
                if old_idx is None or old_idx not in old_to_new:
                    result.extend(new_children)
                    continue
                new_item = OutlineItem(
                    item.title,
                    old_to_new[old_idx],
                    page_location=item.page_location,
                    page_location_args=item.page_location_args,
                )
                new_item.children = new_children
                result.append(new_item)
            except (AttributeError, KeyError):
                new_item = OutlineItem(item.title, dest)
                new_item.children = new_children
                result.append(new_item)
        else:
            new_item = OutlineItem(item.title, dest)
            new_item.children = new_children
            result.append(new_item)
    return result


def map_dimensions_to_pages(pdf: Pdf) -> dict[Dimensions, Pages]:
    """Map page dimensions to page numbers.

    Args:
        pdf: A PDF file.

    Returns:
        A dictionary mapping page dimensions to the set of pages with those
        dimensions.
    """
    ret: dict[Dimensions, Pages] = collections.defaultdict(set)

    for i, page in enumerate(pdf.pages):
        dimensions = _get_dimensions(page)
        ret[dimensions].add(i + 1)

    return ret
