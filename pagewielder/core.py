"""Core functionality for pagewielder."""

import collections
import contextlib
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
        The ``/Names /Dests`` name tree, or None if the file has none.
    """
    names = pdf.Root.get(Name.Names)
    tree = names.get(Name.Dests) if isinstance(names, Dictionary) else None
    if not isinstance(tree, Dictionary):
        return None
    # A NameTree needs an indirect object to wrap, which a file writing its
    # tree into a direct dictionary does not give us.  make_indirect() would
    # convert the tree where it stands, so it is given a copy, and looking a
    # name up leaves the document as it was.
    return NameTree(tree if tree.is_indirect else pdf.make_indirect(tree.copy()))


class _Resolver:
    """Decides which destinations point at the pages one remove_pages() call removes.

    Attributes:
        pdf: The PDF file the destinations belong to.
        removed: Object identifiers of the removed page objects.
        name_tree: The ``/Names /Dests`` name tree, or None if the file has
            none.  It is built once, since over a direct tree that means
            copying the tree.
    """

    def __init__(self, pdf: Pdf, removed: set[_ObjGen]) -> None:
        """Set up a resolver for pdf.

        Args:
            pdf: The PDF file the destinations belong to.
            removed: Object identifiers of the removed page objects.
        """
        self.pdf = pdf
        self.removed = removed
        self.name_tree = _dests_name_tree(pdf)

    def _resolve_named_destination(self, name: Name | String) -> Object | None:
        """Resolve a named destination to the destination it refers to.

        Args:
            name: A ``Name`` (PDF 1.1 style) or ``String`` destination reference.

        Returns:
            The destination the name resolves to, or None if it cannot be found.
        """
        if isinstance(name, Name):
            dests = self.pdf.Root.get(Name.Dests)
            return dests.get(name) if isinstance(dests, Dictionary) else None
        return self.name_tree.get(str(name)) if self.name_tree is not None else None

    def _destination_page(self, dest: Object | int | None) -> Dictionary | None:
        """Find the page object a destination points at, if it can be determined.

        Names and ``/D`` entries are followed in either order and any number of
        times, so that forms like ``<< /S /GoTo /D (someName) >>`` -- an action
        whose destination is a name -- resolve as well as a bare name does.

        Only a ``/GoTo`` action is followed, or a dictionary with no ``/S`` at
        all, which is how ``/Dests`` and the name tree wrap a destination.  Other
        kinds of action are not, since a ``/GoToR`` destination, say, names a page
        in some other file.

        Args:
            dest: A destination, an action containing one, or a reference to a
                named destination.

        Returns:
            The page object the destination targets, or None if it cannot be
            determined.
        """
        for _ in range(_MAX_DESTINATION_HOPS):
            if isinstance(dest, (Name, String)):
                dest = self._resolve_named_destination(dest)
            elif isinstance(dest, Dictionary):
                kind = dest.get(Name.S)
                dest = dest.get(Name.D) if kind is None or kind == Name.GoTo else None
            else:
                break
        if not isinstance(dest, Array) or len(dest) == 0:
            return None
        # Bound to a name, since pyright does not carry an isinstance check on dest[0] over to the next dest[0].
        page = dest[0]
        return page if isinstance(page, Dictionary) else None

    def targets_removed(self, dest: Object | int | None, action: Object | None = None) -> bool:
        """Say whether a target resolves to a removed page.

        Outline items and link annotations carry their target the same way: a
        destination, or failing that an action, which is followed only if it
        is ``/GoTo``.  Document-level destinations pass the destination alone.

        Args:
            dest: The destination, if there is one.
            action: The action, if there is one.

        Returns:
            True if the target is a removed page, False if it is a remaining
            page or cannot be determined.
        """
        page = self._destination_page(action if dest is None else dest)
        return page is not None and page.objgen in self.removed


def _prune_outline_items(resolver: _Resolver, items: list[OutlineItem]) -> list[OutlineItem]:
    """Drop outline items that point at removed pages, promoting their children.

    Args:
        resolver: The resolver for this remove_pages() call.
        items: Outline items at one level of the outline tree.

    Returns:
        The outline items to keep.
    """
    kept: list[OutlineItem] = []
    for item in items:
        item.children = _prune_outline_items(resolver, item.children)
        if resolver.targets_removed(item.destination, item.action):
            kept.extend(item.children)
        else:
            kept.append(item)
    return kept


# Link annotations on the remaining pages that point at removed pages: each
# /Annots array holding one, once, with the indices of its stale links in
# ascending order.
_StaleLinks = list[tuple[Array, list[int]]]


def _stale_links(resolver: _Resolver) -> _StaleLinks:
    """Find the link annotations on the remaining pages that point at removed pages.

    Like a stale document-level destination, such a link leads nowhere and
    keeps the page it names in the saved file.  The link is deleted outright
    rather than stripped of its target, which would leave a clickable region
    that does nothing.

    Args:
        resolver: The resolver for this remove_pages() call.

    Returns:
        The links to delete.
    """

    def is_stale_link(annot: Object) -> bool:
        if not isinstance(annot, Dictionary) or annot.get(Name.Subtype) != Name.Link:
            return False
        return resolver.targets_removed(annot.get(Name.Dest), annot.get(Name.A))

    stale: _StaleLinks = []
    # Pages may share an /Annots array, whose links have to be counted once,
    # or deleting them would delete twice as many.
    seen: set[_ObjGen] = set()
    for page in resolver.pdf.pages:
        annots = page.obj.get(Name.Annots)
        if not isinstance(annots, Array):
            continue
        if annots.is_indirect:
            if annots.objgen in seen:
                continue
            seen.add(annots.objgen)
        indices = [index for index, annot in enumerate(annots.as_list()) if is_stale_link(annot)]
        if indices:
            stale.append((annots, indices))
    return stale


def _delete_links(stale: _StaleLinks) -> None:
    """Delete the links _stale_links() found.

    Args:
        stale: The links to delete.
    """
    for annots, indices in stale:
        for index in reversed(indices):
            del annots[index]


class _StaleDestinations(typing.NamedTuple):
    """Document-level destinations that point at removed pages.

    Attributes:
        dests: Keys of the stale entries in ``/Root /Dests``.
        names: Names of the stale entries in the ``/Root /Names /Dests`` tree.
        open_action: Whether ``/Root /OpenAction`` is stale.
    """

    dests: list[str]
    names: list[str | bytes]
    open_action: bool


def _stale_destinations(resolver: _Resolver) -> _StaleDestinations:
    """Find the document-level destinations that point at removed pages.

    Such destinations no longer lead anywhere, and leaving them in place keeps
    the removed page objects reachable, so they are written out again when the
    file is saved.

    Only ``/Root /Dests``, the ``/Root /Names /Dests`` name tree and
    ``/Root /OpenAction`` are looked at; ``_stale_links()`` sees to the links
    on the remaining pages.

    Args:
        resolver: The resolver for this remove_pages() call.

    Returns:
        The destinations to delete.
    """
    stale_dests: list[str] = []
    stale_names: list[str | bytes] = []

    dests = resolver.pdf.Root.get(Name.Dests)
    if isinstance(dests, Dictionary):
        stale_dests = [key for key in dests.keys() if resolver.targets_removed(dests[key])]

    name_tree = resolver.name_tree
    if name_tree is not None:
        stale_names = [name for name, dest in name_tree.items() if resolver.targets_removed(dest)]

    open_action = resolver.pdf.Root.get(Name.OpenAction)
    stale_open_action = open_action is not None and resolver.targets_removed(open_action)

    return _StaleDestinations(stale_dests, stale_names, stale_open_action)


def _delete_destinations(resolver: _Resolver, stale: _StaleDestinations) -> None:
    """Delete the destinations _stale_destinations() found.

    Args:
        resolver: The resolver for this remove_pages() call.
        stale: The destinations to delete.
    """
    root = resolver.pdf.Root
    dests = root.get(Name.Dests)
    if isinstance(dests, Dictionary):
        for key in stale.dests:
            del dests[key]

    name_tree = resolver.name_tree
    if name_tree is not None and stale.names:
        # The tree may be an indirect copy of a direct one, which has to take
        # its place for the deletions to reach the file.  It is put back only
        # when something is deleted, so a file with nothing to prune keeps
        # the tree it had.
        root.Names[Name.Dests] = name_tree.obj
        for name in stale.names:
            # NameTree finds a name by binary search, which misses entries in
            # a tree whose names are out of order.  Such a tree breaks the
            # spec, and keeping its stale entry beats removing no pages.
            with contextlib.suppress(KeyError):
                del name_tree[name]

    if stale.open_action:
        del root[Name.OpenAction]


def remove_pages(pdf: Pdf, pages: Pages) -> None:
    """Remove the given pages from a PDF in place.

    The outline (table of contents) is kept intact for the remaining pages:
    entries pointing at a removed page are dropped and replaced by their
    children, if any.  Document-level destinations naming a removed page are
    deleted as well.

    ``/PageLabels`` follows the pages it labels: each remaining page keeps
    the label it had, and the ranges are rebuilt against the new page
    indices.

    Link annotations on the remaining pages that point at a removed page are
    deleted.  Other structures that reference pages, such as the structure
    tree and article threads, are left as they are, and a file using them
    keeps the pages they name in the saved file.  The structure tree can
    also go on referring to a deleted link, which then sits on no page.

    Args:
        pdf: A PDF file.
        pages: The set of pages to remove, numbered starting from 1.
    """
    removed: set[_ObjGen] = {page.obj.objgen for number, page in enumerate(pdf.pages, start=1) if number in pages}

    labels = _page_labels(pdf)

    for number in sorted(pages, reverse=True):
        pdf.pages.remove(p=number)

    resolver = _Resolver(pdf, removed)

    # Everything that resolves a target runs before anything is deleted,
    # since a target may be reached through a named destination that is
    # about to go.  The outline is pruned as it is resolved: nothing else
    # reads it.
    stale_links = _stale_links(resolver)
    stale_destinations = _stale_destinations(resolver)
    if Name.Outlines in pdf.Root:
        with pdf.open_outline() as outline:
            outline.root[:] = _prune_outline_items(resolver, outline.root)

    _delete_links(stale_links)
    _delete_destinations(resolver, stale_destinations)

    # A file with no labels to begin with, or labels we cannot read, is left
    # with whatever it had: there is nothing to line back up with the pages.
    if labels:
        _set_page_labels(pdf, [label for number, label in enumerate(labels, start=1) if number not in pages])
