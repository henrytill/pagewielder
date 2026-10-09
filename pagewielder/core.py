"""Core functionality for pagewielder."""

import collections
import contextlib
import typing
from collections.abc import Collection
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


def _tree_object(pdf: Pdf, tree: Dictionary) -> Object:
    """Get an object a ``NameTree`` or ``NumberTree`` can wrap, for a tree in pdf.

    Args:
        pdf: The PDF file the tree belongs to.
        tree: The tree's root dictionary.

    Returns:
        The tree itself if it is indirect, or else an indirect copy of it,
        which ``_delete_from_tree()`` puts in its place.
    """
    # A tree wrapper needs an indirect object, which a file writing its tree
    # into a direct dictionary does not give us.  make_indirect() would
    # convert the tree where it stands, so it is given a copy, and reading
    # the tree leaves the document as it was.
    return tree if tree.is_indirect else pdf.make_indirect(tree.copy())


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

    # Reading the tree is where a malformed one gives out, and a file we
    # cannot label is still a file whose pages we can remove.
    try:
        ranges = list(NumberTree(_tree_object(pdf, tree)).items())
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


@typing.overload
def _delete_from_tree(holder: Object, key: Name, tree: NameTree, stale: Collection[str | bytes]) -> None: ...
@typing.overload
def _delete_from_tree(holder: Object, key: Name, tree: NumberTree, stale: Collection[int]) -> None: ...
def _delete_from_tree(holder: Object, key: Name, tree: NameTree | NumberTree, stale: Collection[typing.Any]) -> None:
    """Delete entries from a tree wrapped over ``_tree_object()``.

    The tree may wrap an indirect copy of a direct one, which has to take
    its place for the deletions to reach the file.  It is put back only
    when something is deleted, so a file with nothing to prune keeps the
    tree it had.

    Args:
        holder: The dictionary holding the tree.
        key: The key the tree is held under.
        tree: The tree.
        stale: The keys of the entries to delete.
    """
    if not stale:
        return
    holder[key] = tree.obj
    for name in stale:
        # A tree finds a key by binary search, which misses entries in a
        # tree whose keys are out of order.  Such a tree breaks the spec,
        # and keeping its stale entry beats removing no pages.
        with contextlib.suppress(KeyError):
            del tree[name]


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
    return NameTree(_tree_object(pdf, tree))


class _Resolver:
    """Decides which targets point at the pages one remove_pages() call removes.

    The named destinations are read once, when the resolver is made, so that
    deleting a stale one later cannot change how anything else resolves.
    The pruners can therefore run, and delete as they go, in any order.

    ``pdf`` and ``removed_pages`` are kept as given to ``__init__``.

    Attributes:
        removed: Object identifiers of the removed page objects.
        stale_links: The link annotations on the remaining pages that point
            at removed pages, found up front for the same reason: the
            structure tree has to know which links ``_prune_links()``
            deletes, whether it is pruned before or after them.
        name_tree: The ``/Names /Dests`` name tree, or None if the file has
            none.  It is built once, since over a direct tree that means
            copying the tree.
    """

    def __init__(self, pdf: Pdf, removed_pages: list[Dictionary]) -> None:
        """Set up a resolver for pdf.

        Args:
            pdf: The PDF file the targets belong to.
            removed_pages: The removed page objects.
        """
        self.pdf = pdf
        self.removed_pages = removed_pages
        self.removed: set[_ObjGen] = {page.objgen for page in removed_pages}
        self.name_tree = _dests_name_tree(pdf)
        dests = pdf.Root.get(Name.Dests)
        self._dests: dict[str, Object] = (
            {key: dests[key] for key in dests.keys()} if isinstance(dests, Dictionary) else {}
        )
        self._names: dict[str | bytes, Object] = dict(self.name_tree.items()) if self.name_tree is not None else {}
        self.stale_links: list[Object] = []
        for page in pdf.pages:
            annots = page.obj.get(Name.Annots)
            if isinstance(annots, Array):
                self.stale_links += [annot for annot in annots.as_list() if self.is_stale_link(annot)]

    def _resolve_named_destination(self, name: Name | String) -> Object | None:
        """Resolve a named destination to the destination it refers to.

        Args:
            name: A ``Name`` (PDF 1.1 style) or ``String`` destination reference.

        Returns:
            The destination the name resolves to, or None if it cannot be found.
        """
        # Not cached: each hop is a dict lookup in the copies, about what a
        # cache lookup would cost, and a cache would need keys that keep a
        # Name and a String of the same text apart.
        return (self._dests if isinstance(name, Name) else self._names).get(str(name))

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
        return self.is_removed(self._destination_page(action if dest is None else dest))

    def is_stale_link(self, annot: Object) -> bool:
        """Say whether an annotation is a link pointing at a removed page.

        Args:
            annot: An annotation.

        Returns:
            True if annot is a link whose target is a removed page.
        """
        if not isinstance(annot, Dictionary) or annot.get(Name.Subtype) != Name.Link:
            return False
        return self.targets_removed(annot.get(Name.Dest), annot.get(Name.A))

    def is_removed(self, page: Object | None) -> bool:
        """Say whether page is a removed page.

        Args:
            page: A page object, or whatever stands in its place.

        Returns:
            True if page is one of the removed page objects.
        """
        return isinstance(page, Dictionary) and page.objgen in self.removed


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


def _prune_links(resolver: _Resolver) -> None:
    """Delete link annotations on the remaining pages that point at removed pages.

    Like a stale document-level destination, such a link leads nowhere and
    keeps the page it names in the saved file.  The link is deleted outright
    rather than stripped of its target, which would leave a clickable region
    that does nothing.

    Args:
        resolver: The resolver for this remove_pages() call.
    """
    # A page sharing an /Annots array already pruned for another finds
    # nothing left to delete.
    for page in resolver.pdf.pages:
        annots = page.obj.get(Name.Annots)
        if not isinstance(annots, Array):
            continue
        stale = [index for index, annot in enumerate(annots.as_list()) if resolver.is_stale_link(annot)]
        for index in reversed(stale):
            del annots[index]


def _prune_destinations(resolver: _Resolver) -> None:
    """Drop document-level destinations that point at removed pages.

    Such destinations no longer lead anywhere, and leaving them in place keeps
    the removed page objects reachable, so they are written out again when the
    file is saved.

    Only ``/Root /Dests``, the ``/Root /Names /Dests`` name tree and
    ``/Root /OpenAction`` are pruned; ``_prune_links()`` sees to the links on
    the remaining pages.

    Args:
        resolver: The resolver for this remove_pages() call.
    """
    root = resolver.pdf.Root

    # Read again rather than taken from the resolver, which holds a copy of
    # the entries: the deletions have to reach the dictionary itself.
    dests = root.get(Name.Dests)
    if isinstance(dests, Dictionary):
        for key in [key for key in dests.keys() if resolver.targets_removed(dests[key])]:
            del dests[key]

    name_tree = resolver.name_tree
    if name_tree is not None:
        # Collected first, since a tree cannot be changed while it is walked.
        stale_names = [name for name, dest in name_tree.items() if resolver.targets_removed(dest)]
        _delete_from_tree(root.Names, Name.Dests, name_tree, stale_names)

    open_action = root.get(Name.OpenAction)
    if open_action is not None and resolver.targets_removed(open_action):
        del root[Name.OpenAction]


class _StructTreePruner:
    """Walks a structure tree, dropping what belongs to removed pages or pruned links."""

    def __init__(self, resolver: _Resolver) -> None:
        """Set up a pruner.

        Args:
            resolver: The resolver for this remove_pages() call.
        """
        self._resolver = resolver
        # Whether each indirect element reached was kept.  An element is
        # entered as kept, so that a malformed tree looping back to it ends
        # there, and one reached twice is decided once.
        self._kept: dict[_ObjGen, bool] = {}

    @property
    def dropped(self) -> set[_ObjGen]:
        """Object identifiers of the indirect elements dropped."""
        return {objgen for objgen, kept in self._kept.items() if not kept}

    def prune_kids(self, holder: Dictionary, page: Object | None) -> bool:
        """Drop the kids of holder that belong to removed pages or pruned links.

        Args:
            holder: The structure tree root or an element.
            page: The page holder's kids are on unless they say otherwise.

        Returns:
            False if holder had kids and none is left, True otherwise.
        """
        kids = holder.get(Name.K)
        if kids is None:
            return True
        items = kids.as_list() if isinstance(kids, Array) else [kids]
        kept = [kid for kid in items if self._keep_kid(kid, page)]
        if len(kept) < len(items):
            holder.K = Array(kept)
        return bool(kept)

    def _keep_kid(self, kid: Object | int, page: Object | None) -> bool:
        # A kid without a /Pg of its own is on its element's page.
        if isinstance(kid, int):
            return not self._resolver.is_removed(page)
        if not isinstance(kid, Dictionary):
            return True
        kind = kid.get(Name.Type)
        if kind == Name.MCR:
            return not self._resolver.is_removed(kid.get(Name.Pg, page))
        if kind == Name.OBJR:
            return self._keep_object_reference(kid, page)
        return self._keep_element(kid)

    def _keep_object_reference(self, objr: Dictionary, page: Object | None) -> bool:
        if self._resolver.is_removed(objr.get(Name.Pg, page)):
            return False
        annot = objr.get(Name.Obj)
        if not isinstance(annot, Dictionary):
            return True
        return not (self._resolver.is_removed(annot.get(Name.P)) or self._resolver.is_stale_link(annot))

    def _keep_element(self, elem: Dictionary) -> bool:
        if elem.is_indirect:
            if elem.objgen in self._kept:
                return self._kept[elem.objgen]
            self._kept[elem.objgen] = True
        page = elem.get(Name.Pg)
        if not self.prune_kids(elem, page):
            if elem.is_indirect:
                self._kept[elem.objgen] = False
            return False
        # Whatever named this page through the element has just gone, and
        # the /Pg would otherwise keep the page in the file.
        if self._resolver.is_removed(page):
            del elem.Pg
        return True


def _stale_parent_tree_keys(resolver: _Resolver) -> set[int]:
    """Find the ``/ParentTree`` keys of what remove_pages() removes.

    They are keyed by what goes: the removed pages, by ``/StructParents``,
    and their annotations and the links pruned from the remaining pages, by
    ``/StructParent``.

    Args:
        resolver: The resolver for this remove_pages() call.

    Returns:
        The keys whose entries are to go.
    """
    owners: list[tuple[Object, Name]] = []
    for page in resolver.removed_pages:
        owners.append((page, Name.StructParents))
        annots = page.get(Name.Annots)
        if isinstance(annots, Array):
            owners += [(annot, Name.StructParent) for annot in annots.as_list()]
    owners += [(annot, Name.StructParent) for annot in resolver.stale_links]
    keys: set[int] = set()
    for owner, key in owners:
        number = owner.get(key) if isinstance(owner, Dictionary) else None
        if isinstance(number, int):
            keys.add(number)
    return keys


def _prune_struct_tree(resolver: _Resolver) -> None:
    """Drop the parts of the structure tree that belong to removed pages or pruned links.

    Marked content on a removed page goes, as do object references to
    annotations on a removed page and to the links ``_prune_links()``
    deletes.  An element left with nothing in it goes too, and so on up the
    tree; its children are not promoted as outline items are, since an
    element's role is part of what the document means.  An element that
    stays keeps no ``/Pg`` naming a removed page, and ``/ParentTree`` and
    ``/IDTree`` lose their entries for what was dropped, since each would
    otherwise keep the removed pages in the saved file.

    A root left with no elements stays, empty, along with ``/MarkInfo``.

    Args:
        resolver: The resolver for this remove_pages() call.
    """
    pdf = resolver.pdf
    root = pdf.Root.get(Name.StructTreeRoot)
    if not isinstance(root, Dictionary):
        return

    pruner = _StructTreePruner(resolver)
    # Whether anything is left does not matter here: an empty root stays.
    pruner.prune_kids(root, None)
    dropped = pruner.dropped

    stale_keys = _stale_parent_tree_keys(resolver)
    parent_tree = root.get(Name.ParentTree)
    if isinstance(parent_tree, Dictionary):
        _delete_from_tree(root, Name.ParentTree, NumberTree(_tree_object(pdf, parent_tree)), stale_keys)

    id_tree = root.get(Name.IDTree)
    if isinstance(id_tree, Dictionary) and dropped:
        ids = NameTree(_tree_object(pdf, id_tree))
        _delete_from_tree(root, Name.IDTree, ids, [name for name, elem in ids.items() if elem.objgen in dropped])


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
    deleted, and the structure tree of a tagged PDF loses what belonged to
    the removed pages and those links.  Article threads are left as they
    are, and a file using them keeps the pages they name in the saved file.

    Args:
        pdf: A PDF file.
        pages: The set of pages to remove, numbered starting from 1.
    """
    removed_pages = [page.obj for number, page in enumerate(pdf.pages, start=1) if number in pages]

    labels = _page_labels(pdf)

    for number in sorted(pages, reverse=True):
        pdf.pages.remove(p=number)

    resolver = _Resolver(pdf, removed_pages)

    if Name.Outlines in pdf.Root:
        with pdf.open_outline() as outline:
            outline.root[:] = _prune_outline_items(resolver, outline.root)

    _prune_links(resolver)
    _prune_destinations(resolver)
    _prune_struct_tree(resolver)

    # A file with no labels to begin with, or labels we cannot read, is left
    # with whatever it had: there is nothing to line back up with the pages.
    if labels:
        _set_page_labels(pdf, [label for number, label in enumerate(labels, start=1) if number not in pages])
