"""Core functionality for pagewielder."""

import collections
import typing
from decimal import Decimal

from pikepdf import (
    Array,
    Dictionary,
    Name,
    NameTree,
    NumberTree,
    Object,
    OutlineItem,
    Page,
    Pdf,
    PdfError,
    Rectangle,
    String,
)

Dimensions = tuple[float, float]
Pages = set[int]
_ObjGen = tuple[int, int]


class _PageLabel(typing.NamedTuple):
    """The label a ``/PageLabels`` range gives to one page.

    Attributes:
        style: The numbering style, ``/S``, or None if the range numbers
            nothing and every page in it carries the prefix alone.
        prefix: The label prefix, ``/P``, or None if the range has none.
        number: The number this page takes within its range, which counts
            for nothing when the range has no numbering style.
    """

    style: Object | None
    prefix: Object | None
    number: int


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

    for number, page in enumerate(pdf.pages, start=1):
        dimensions = _get_dimensions(page)
        ret[dimensions].add(number)

    return ret


def _page_labels(pdf: Pdf) -> list[_PageLabel | None]:
    """Work out the label in force for each page of a document.

    Args:
        pdf: A PDF file.

    Returns:
        One entry per page, in page order, holding that page's label or None
        if no range covers it, or an empty list if the file has no usable
        ``/PageLabels``.
    """
    tree = pdf.Root.get(Name.PageLabels)
    if not isinstance(tree, Dictionary):
        return []

    # A NumberTree needs an indirect object to wrap, which a file writing its
    # ranges into a direct dictionary does not give us.  Reading one is also
    # where a malformed tree gives out, and a file we cannot label is still a
    # file whose pages we can remove.
    try:
        ranges = list(NumberTree(pdf.make_indirect(tree)).items())
    except PdfError:
        return []
    count = len(pdf.pages)
    labels: list[_PageLabel | None] = [None] * count

    for position, (start, entry) in enumerate(ranges):
        end = ranges[position + 1][0] if position + 1 < len(ranges) else count
        if not isinstance(entry, Dictionary):
            continue
        style = entry.get(Name.S)
        prefix = entry.get(Name.P)
        # /St is an integer, but a file writing it as a real still means a
        # number, and renumbering the range from 1 would rewrite its labels.
        start_number = entry.get(Name.St)
        first = int(start_number) if isinstance(start_number, (int, Decimal)) else 1
        for index in range(max(start, 0), min(end, count)):
            labels[index] = _PageLabel(style, prefix, first + index - start)

    return labels


def _continues(previous: _PageLabel, label: _PageLabel) -> bool:
    """Report whether a label carries on the range the previous one belongs to.

    Args:
        previous: The label of the preceding page.
        label: The label of the page in question.

    Returns:
        True if a single range can describe both pages.
    """
    if previous.style != label.style or previous.prefix != label.prefix:
        return False
    return label.style is None or label.number == previous.number + 1


def _set_page_labels(pdf: Pdf, labels: list[_PageLabel | None]) -> None:
    """Replace the document's ``/PageLabels`` with the given per-page labels.

    Consecutive pages whose labels run on from one another are written as a
    single range, so a document whose pages were left alone comes back out
    with the ranges it went in with.  A document with nothing left to label
    loses its ``/PageLabels`` altogether.

    Args:
        pdf: A PDF file.
        labels: One entry per page, in page order, holding that page's label
            or None if the page is to fall outside every range.
    """
    tree = NumberTree.new(pdf)
    previous: _PageLabel | None = None

    for index, label in enumerate(labels):
        if label is None:
            previous = None
            continue
        if previous is None or not _continues(previous, label):
            entry = Dictionary()
            if label.style is not None:
                entry[Name.S] = label.style
                if label.number != 1:
                    entry[Name.St] = label.number
            if label.prefix is not None:
                entry[Name.P] = label.prefix
            tree[index] = entry
        previous = label

    if len(tree.obj.Nums) > 0:
        pdf.Root[Name.PageLabels] = tree.obj
    elif Name.PageLabels in pdf.Root:
        del pdf.Root[Name.PageLabels]


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

    ``/PageLabels`` follows the pages it labels: each remaining page keeps
    the label it had, and the ranges are rebuilt against the new page
    indices.

    Other structures that reference pages are left as they are, and a file
    using them will not come out clean: link annotations on the remaining
    pages can still name a removed page, which also keeps that page in the
    saved file.

    Args:
        pdf: A PDF file.
        pages: The set of pages to remove, numbered starting from 1.
    """
    removed: set[_ObjGen] = {page.obj.objgen for number, page in enumerate(pdf.pages, start=1) if number in pages}

    labels = _page_labels(pdf)

    for number in sorted(pages, reverse=True):
        pdf.pages.remove(p=number)

    if Name.Outlines in pdf.Root:
        with pdf.open_outline() as outline:
            outline.root[:] = _prune_outline_items(pdf, outline.root, removed)

    _prune_destinations(pdf, removed)

    # A file with no labels to begin with, or labels we cannot read, is left
    # with whatever it had: there is nothing to line back up with the pages.
    if labels:
        _set_page_labels(pdf, [label for number, label in enumerate(labels, start=1) if number not in pages])
