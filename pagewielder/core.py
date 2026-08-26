"""Core functionality for pagewielder."""

import collections
from typing import Optional

from pikepdf import Array, Dictionary, Name, NameTree, Object, OutlineItem, Page, Pdf, Rectangle, String

Dimensions = tuple[float, float]
Pages = set[int]
_ObjGen = tuple[int, int]


def _get_dimensions(page: Page) -> Dimensions:
    """Get the dimensions of a page in a PDF file.

    Args:
        page: A page in a PDF file.

    Returns:
        The dimensions of the page.
    """
    rect = Rectangle(page.mediabox)
    return (rect.width, rect.height)


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


def _resolve_named_destination(pdf: Pdf, name: Name | String) -> Optional[Object]:
    """Resolve a named destination to the destination it refers to.

    Args:
        pdf: The PDF file the destination belongs to.
        name: A ``Name`` (PDF 1.1 style) or ``String`` destination reference.

    Returns:
        The destination the name resolves to, or None if it cannot be found.
    """
    if isinstance(name, Name):
        dests = pdf.Root.get(Name.Dests)
        return dests.get(name) if isinstance(dests, Dictionary) else None
    names = pdf.Root.get(Name.Names)
    tree = names.get(Name.Dests) if isinstance(names, Dictionary) else None
    return NameTree(tree).get(str(name)) if tree is not None else None


def _destination_page(pdf: Pdf, item: OutlineItem) -> Optional[Dictionary]:
    """Find the page object an outline item points at, if it can be determined.

    Args:
        pdf: The PDF file the outline item belongs to.
        item: An outline item.

    Returns:
        The page object the item targets, or None if it cannot be determined.
    """
    dest: Object | int | None = item.destination
    if dest is None and isinstance(item.action, Dictionary) and item.action.get(Name.S) == Name.GoTo:
        dest = item.action.get(Name.D)
    if isinstance(dest, (Name, String)):
        dest = _resolve_named_destination(pdf, dest)
    if isinstance(dest, Dictionary):
        dest = dest.get(Name.D)
    if isinstance(dest, Array) and len(dest) > 0 and isinstance(dest[0], Dictionary):
        return dest[0]
    return None


def _prune_outline_items(pdf: Pdf, items: list[OutlineItem], removed: set[_ObjGen]) -> list[OutlineItem]:
    """Drop outline items that point at removed pages, promoting their children.

    Args:
        pdf: The PDF file the outline belongs to.
        items: Outline items at one level of the outline tree.
        removed: Object identifiers of the removed page objects.

    Returns:
        The outline items to keep.
    """
    kept: list[OutlineItem] = []
    for item in items:
        item.children = _prune_outline_items(pdf, item.children, removed)
        page = _destination_page(pdf, item)
        if page is not None and page.objgen in removed:
            kept.extend(item.children)
        else:
            kept.append(item)
    return kept


def remove_pages(pdf: Pdf, pages: Pages) -> None:
    """Remove the given pages from a PDF in place.

    Document-level features such as the outline (table of contents) are kept
    intact for the remaining pages.  Outline entries that point at a removed
    page are dropped and replaced by their children, if any.

    Args:
        pdf: A PDF file.
        pages: The set of pages to remove, numbered starting from 1.
    """
    removed: set[_ObjGen] = {page.obj.objgen for number, page in enumerate(pdf.pages, start=1) if number in pages}

    for number in sorted(pages, reverse=True):
        pdf.pages.remove(p=number)

    if Name.Outlines in pdf.Root:
        with pdf.open_outline() as outline:
            outline.root[:] = _prune_outline_items(pdf, outline.root, removed)
