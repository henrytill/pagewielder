"""Core functionality for pagewielder."""

import collections

from pikepdf import Array, Dictionary, Name, NameTree, Object, OutlineItem, Page, Pdf, Rectangle, String

Dimensions = tuple[float, float]
Pages = set[int]
_ObjGen = tuple[int, int]

# A destination may need several hops to reach an array: an action holds its
# destination under /D, and that destination may itself be a name.  Bounding
# the hops keeps a name that resolves back to itself from looping forever.
_MAX_DESTINATION_HOPS = 8


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


def _dests_name_tree(pdf: Pdf) -> NameTree | None:
    """Get the name tree holding the document's named destinations.

    Args:
        pdf: A PDF file.

    Returns:
        The ``/Names /Dests`` name tree, or None if the file has none or its
        root is not an indirect dictionary, which is what ``NameTree`` needs
        in order to wrap it.
    """
    names = pdf.Root.get(Name.Names)
    tree = names.get(Name.Dests) if isinstance(names, Dictionary) else None
    if not isinstance(tree, Dictionary) or not tree.is_indirect:
        return None
    return NameTree(tree)


def _resolve_named_destination(pdf: Pdf, name: Name | String) -> Object | None:
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
    tree = _dests_name_tree(pdf)
    return tree.get(str(name)) if tree is not None else None


def _destination_page(pdf: Pdf, dest: Object | int | None) -> Dictionary | None:
    """Find the page object a destination points at, if it can be determined.

    Names and ``/D`` entries are followed in either order and any number of
    times, so that forms like ``<< /S /GoTo /D (someName) >>`` -- an action
    whose destination is a name -- resolve as well as a bare name does.

    Args:
        pdf: The PDF file the destination belongs to.
        dest: A destination, an action containing one, or a reference to a
            named destination.

    Returns:
        The page object the destination targets, or None if it cannot be
        determined.
    """
    for _ in range(_MAX_DESTINATION_HOPS):
        if isinstance(dest, (Name, String)):
            dest = _resolve_named_destination(pdf, dest)
        elif isinstance(dest, Dictionary):
            dest = dest.get(Name.D)
        else:
            break
    if isinstance(dest, Array) and len(dest) > 0 and isinstance(dest[0], Dictionary):
        return dest[0]
    return None


def _outline_item_page(pdf: Pdf, item: OutlineItem) -> Dictionary | None:
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
    return _destination_page(pdf, dest)


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
        page = _outline_item_page(pdf, item)
        if page is not None and page.objgen in removed:
            kept.extend(item.children)
        else:
            kept.append(item)
    return kept


def _prune_destinations(pdf: Pdf, removed: set[_ObjGen]) -> None:
    """Drop document-level destinations that point at removed pages.

    Such destinations no longer lead anywhere, and leaving them in place keeps
    the removed page objects reachable, so they are written out again when the
    file is saved.

    Only ``/Root /Dests``, the ``/Root /Names /Dests`` name tree and
    ``/Root /OpenAction`` are pruned.  Link annotations on the remaining pages
    are a more common way to reference a page and are not touched, so a file
    carrying those keeps both the dangling links and the pages they name.

    Args:
        pdf: The PDF file the destinations belong to.
        removed: Object identifiers of the removed page objects.
    """

    def targets_removed_page(dest: Object) -> bool:
        page = _destination_page(pdf, dest)
        return page is not None and page.objgen in removed

    # Decide everything before deleting anything: resolving a name needs the
    # entry it names to still be in the tree.
    stale_dests: list[str] = []
    stale_names: list[str | bytes] = []

    dests = pdf.Root.get(Name.Dests)
    if isinstance(dests, Dictionary):
        stale_dests = [key for key in dests.keys() if targets_removed_page(dests[key])]

    name_tree = _dests_name_tree(pdf)
    if name_tree is not None:
        stale_names = [name for name, dest in name_tree.items() if targets_removed_page(dest)]

    open_action = pdf.Root.get(Name.OpenAction)
    stale_open_action = open_action is not None and targets_removed_page(open_action)

    if isinstance(dests, Dictionary):
        for key in stale_dests:
            del dests[key]

    if name_tree is not None:
        for name in stale_names:
            del name_tree[name]

    if stale_open_action:
        del pdf.Root[Name.OpenAction]


def remove_pages(pdf: Pdf, pages: Pages) -> None:
    """Remove the given pages from a PDF in place.

    The outline (table of contents) is kept intact for the remaining pages:
    entries pointing at a removed page are dropped and replaced by their
    children, if any.  Document-level destinations naming a removed page are
    deleted as well.

    Other structures that reference pages are left as they are, and a file
    using them will not come out clean: link annotations on the remaining
    pages can still name a removed page, which also keeps that page in the
    saved file, and ``/PageLabels`` is carried over unchanged, so its labels
    no longer line up with the pages they describe.

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

    _prune_destinations(pdf, removed)
