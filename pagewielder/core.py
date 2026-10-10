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

# Field types are inherited down the field tree, and looking one up follows
# /Parent; bounding the climb keeps a /Parent loop from running forever.
_MAX_FIELD_DEPTH = 64


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


@typing.overload
def _read_tree(
    pdf: Pdf, tree: Object | None, kind: type[NameTree]
) -> tuple[NameTree, dict[str | bytes, Object]] | None: ...
@typing.overload
def _read_tree(
    pdf: Pdf, tree: Object | None, kind: type[NumberTree]
) -> tuple[NumberTree, dict[int, Object]] | None: ...
def _read_tree(
    pdf: Pdf, tree: Object | None, kind: type[NameTree] | type[NumberTree]
) -> tuple[NameTree | NumberTree, dict[typing.Any, Object]] | None:
    """Wrap a name or number tree over ``_tree_object()`` and read its entries.

    Args:
        pdf: The PDF file the tree belongs to.
        tree: The tree's root dictionary, or whatever stands in its place.
        kind: ``NameTree`` or ``NumberTree``.

    Returns:
        The wrapped tree and its entries, or None if tree is not a
        dictionary or cannot be read.
    """
    if not isinstance(tree, Dictionary):
        return None
    wrapped = kind(_tree_object(pdf, tree))
    # Reading a tree is where a malformed one gives out.  It is then treated
    # as missing, and left as it is: a file with a tree we cannot read is
    # still a file whose pages we can remove.
    try:
        return wrapped, dict(wrapped.items())
    except PdfError:
        return None


def _page_labels(pdf: Pdf) -> list[_PageLabel | None]:
    """Work out the label in force for each page of a document.

    Args:
        pdf: A PDF file.

    Returns:
        One entry per page, in page order, holding that page's label or None
        if no range covers it, or an empty list if the file has no usable
        ``/PageLabels``.
    """
    read = _read_tree(pdf, pdf.Root.get(Name.PageLabels), NumberTree)
    if read is None:
        return []
    ranges = list(read[1].items())
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
        # tree whose keys are out of order, and may trip over a malformed
        # part the read did not.  Such a tree breaks the spec, and keeping
        # its stale entry beats removing no pages.
        with contextlib.suppress(KeyError, PdfError):
            del tree[name]


def _dests_name_tree(pdf: Pdf) -> tuple[NameTree, dict[str | bytes, Object]] | None:
    """Get and read the name tree holding the document's named destinations.

    Args:
        pdf: A PDF file.

    Returns:
        The ``/Names /Dests`` name tree and its entries, or None if the file
        has none or it cannot be read.
    """
    names = pdf.Root.get(Name.Names)
    return _read_tree(pdf, names.get(Name.Dests) if isinstance(names, Dictionary) else None, NameTree)


def _indirect_id(obj: object) -> _ObjGen | None:
    """Get the object identifier of an indirect object.

    Args:
        obj: Anything an array or dictionary may hold.

    Returns:
        The identifier, or None if obj is not an indirect object.
    """
    return obj.objgen if isinstance(obj, Object) and obj.is_indirect else None


def _page_annotations(pdf: Pdf) -> typing.Iterator[tuple[Dictionary, Array]]:
    """Yield each page of pdf that has an ``/Annots`` array, with the array.

    Args:
        pdf: A PDF file.

    Yields:
        Each page object and its ``/Annots`` array, in page order.
    """
    for page in pdf.pages:
        annots = page.obj.get(Name.Annots)
        if isinstance(annots, Array):
            yield page.obj, annots


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
        removed_annots: The annotations on the removed pages that are on no
            remaining page, which a page sharing an ``/Annots`` array or an
            annotation with a removed one keeps.
        name_tree: The ``/Names /Dests`` name tree, or None if the file has
            none or it cannot be read.  It is built once, since over a
            direct tree that means copying the tree.
        names: The entries of name_tree, read once.
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
        read = _dests_name_tree(pdf)
        self.name_tree: NameTree | None = None
        self.names: dict[str | bytes, Object] = {}
        if read is not None:
            self.name_tree, self.names = read
        dests = pdf.Root.get(Name.Dests)
        self._dests: dict[str, Object] = (
            {key: dests[key] for key in dests.keys()} if isinstance(dests, Dictionary) else {}
        )
        self.stale_links: list[Object] = []
        # Indirect /Annots arrays and annotations on the remaining pages.
        placed: set[_ObjGen | None] = set()
        for _, annots in _page_annotations(pdf):
            placed |= {_indirect_id(obj) for obj in [annots, *annots.as_list()]}
            self.stale_links += [annot for annot in annots.as_list() if self.is_stale_link(annot)]
        placed.discard(None)
        self._placed_annots = placed
        self.removed_annots: list[Object] = []
        for removed_page in removed_pages:
            removed_annots = removed_page.get(Name.Annots)
            if isinstance(removed_annots, Array) and _indirect_id(removed_annots) not in placed:
                self.removed_annots += [
                    annot for annot in removed_annots.as_list() if _indirect_id(annot) not in placed
                ]
        self._removed_annot_ids = {_indirect_id(annot) for annot in self.removed_annots} - {None}

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
        return (self._dests if isinstance(name, Name) else self.names).get(str(name))

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

    def is_removed_annotation(self, obj: Object | None) -> bool:
        """Say whether obj is one of the removed annotations.

        Args:
            obj: Any object.

        Returns:
            True if obj is in ``removed_annots``.
        """
        return obj is not None and _indirect_id(obj) in self._removed_annot_ids

    def went_with_removed_pages(self, obj: Object | None) -> bool:
        """Say whether obj is an annotation that went with the removed pages.

        An annotation is placed by the ``/Annots`` holding it, whatever its
        ``/P`` says, since ``/P`` may be missing or name a page sharing it.
        One no page's ``/Annots`` holds has only its ``/P`` to go by.

        Args:
            obj: Any object.

        Returns:
            True if obj is in ``removed_annots``, or is held by no page and
            names a removed page as its ``/P``.
        """
        if self.is_removed_annotation(obj):
            return True
        return isinstance(obj, Dictionary) and not self.is_placed_annotation(obj) and self.is_removed(obj.get(Name.P))

    def is_placed_annotation(self, obj: Object | None) -> bool:
        """Say whether obj is an annotation on a remaining page.

        Args:
            obj: Any object.

        Returns:
            True if obj is an indirect annotation in a remaining page's ``/Annots``.
        """
        return obj is not None and _indirect_id(obj) in self._placed_annots

    def is_stale_link(self, annot: Object | None) -> bool:
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
    # nothing left to delete.  Each link is decided again rather than
    # looked up in resolver.stale_links: deciding is a few dict lookups, the
    # answer cannot differ, and a direct link has no identifier to look up.
    for _, annots in _page_annotations(resolver.pdf):
        stale = [index for index, annot in enumerate(annots.as_list()) if resolver.is_stale_link(annot)]
        for index in reversed(stale):
            del annots[index]


def _repoint_annotations(resolver: _Resolver) -> None:
    """Point annotations on the remaining pages away from removed pages.

    An annotation a removed page shared with a remaining one, by sharing
    the annotation or its whole ``/Annots`` array, may name the removed page
    as its ``/P``, which would keep that page in the saved file.  Its
    ``/P`` becomes the first remaining page holding it, which is as much
    its page as any other that shares it.

    Args:
        resolver: The resolver for this remove_pages() call.
    """
    for page, annots in _page_annotations(resolver.pdf):
        for annot in annots.as_list():
            if isinstance(annot, Dictionary) and resolver.is_removed(annot.get(Name.P)):
                annot.P = page


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
        stale_names = [name for name, dest in resolver.names.items() if resolver.targets_removed(dest)]
        _delete_from_tree(root.Names, Name.Dests, name_tree, stale_names)

    open_action = root.get(Name.OpenAction)
    if open_action is not None and resolver.targets_removed(open_action):
        del root[Name.OpenAction]


class _KidsFrame(typing.NamedTuple):
    """A node of a tree, or its root, whose kids are being pruned.

    Attributes:
        holder: The node or root.
        key: The key holder keeps its kids under.
        page: The page holder's kids are on unless they say otherwise, for
            a structure element.
        items: Holder's kids, as they were.
        pending: The kids still to be decided, with their positions in
            items.
        kept: The kids decided so far to keep.
        positions: The positions in items of the kids in kept, since a
            direct kid has no identifier to find it by.
        position: Holder's own position among its parent's kids.
    """

    holder: Dictionary
    key: Name
    page: Object | None
    items: list[Object | int]
    pending: typing.Iterator[tuple[int, Object | int]]
    kept: list[Object | int]
    positions: list[int]
    position: int = -1

    def keep(self, kid: Object | int, index: int) -> None:
        """Keep a kid.

        Args:
            kid: The kid.
            index: Its position in items.
        """
        self.kept.append(kid)
        self.positions.append(index)


def _kids_frame(holder: Dictionary, key: Name, page: Object | None = None) -> _KidsFrame | None:
    """Start pruning holder's kids, if it has any.

    Args:
        holder: A node of a tree, or its root.
        key: The key holder keeps its kids under.
        page: The page holder's kids are on unless they say otherwise.

    Returns:
        A frame for holder, or None if it has no kids: a node that had none
        lost none to the removed pages, and is kept as it is.
    """
    kids = holder.get(key)
    if kids is None:
        return None
    # pikepdf hands back an MCID as an int, whatever its stubs say.
    items: list[Object | int] = [*kids.as_list()] if isinstance(kids, Array) else [kids]
    if not items:
        return None
    return _KidsFrame(holder, key, page, items, enumerate(items), [], [])


def _set_kids(frame: _KidsFrame) -> None:
    """Write back the kids frame kept, if it dropped any.

    Args:
        frame: A frame whose kids are all decided.
    """
    if len(frame.kept) < len(frame.items):
        frame.holder[frame.key] = Array(frame.kept)


class _TreePruner:
    """Walks a tree depth first, dropping nodes left with no kids.

    A node can be decided only once all its kids are, so the walk keeps its
    own stack of the nodes part way through, rather than recursing: a tree
    deep enough to exhaust Python's stack is still a tree whose pages we can
    remove.  Subclasses say what each kid is through ``_enter()``,
    ``_keep()`` and ``_leave()``.
    """

    def __init__(self, resolver: _Resolver) -> None:
        """Set up a pruner.

        Args:
            resolver: The resolver for this remove_pages() call.
        """
        self._resolver = resolver
        # Whether each indirect node reached was kept.  A node is entered as
        # kept, so that a malformed tree looping back to it ends there, and
        # one reached twice is decided once.
        self._kept: dict[_ObjGen, bool] = {}

    @property
    def dropped(self) -> set[_ObjGen]:
        """Object identifiers of the indirect nodes dropped."""
        return {objgen for objgen, kept in self._kept.items() if not kept}

    def _prune(self, start: _KidsFrame | None) -> None:
        """Prune the tree from its root's frame.  A root left with no kids stays.

        Args:
            start: The root's frame, or None if it has no kids.
        """
        stack = [start] if start is not None else []
        while stack:
            frame = stack[-1]
            for index, kid in frame.pending:
                child = self._enter(kid)
                if child is not None:
                    stack.append(child._replace(position=index))
                    break
                if self._keep(kid, frame):
                    frame.keep(kid, index)
            else:
                stack.pop()
                if not stack:
                    _set_kids(frame)
                elif self._leave(frame):
                    stack[-1].keep(frame.holder, frame.position)

    def _first_visit(self, node: Object) -> bool:
        """Note node as entered, saying whether it was entered before.

        Args:
            node: A node about to be walked.

        Returns:
            False if node was reached before, True otherwise.
        """
        ident = _indirect_id(node)
        if ident is None:
            return True
        if ident in self._kept:
            return False
        self._kept[ident] = True
        return True

    def _was_kept(self, node: Object | int) -> bool:
        """Say whether a node not walked here is kept.

        Args:
            node: A kid with no kids of its own, or one decided already.

        Returns:
            False if node was dropped, True otherwise.
        """
        ident = _indirect_id(node)
        return ident is None or self._kept.get(ident, True)

    def _drop(self, node: Object) -> None:
        """Note node as dropped.

        Args:
            node: The node dropped.
        """
        ident = _indirect_id(node)
        if ident is not None:
            self._kept[ident] = False

    def _enter(self, kid: Object | int) -> _KidsFrame | None:
        """Start walking kid, if it is a node with kids not yet walked.

        Args:
            kid: A kid of the node on top of the stack.

        Returns:
            A frame for kid, or None to decide it with ``_keep()``.
        """
        raise NotImplementedError

    def _keep(self, kid: Object | int, frame: _KidsFrame) -> bool:
        """Decide a kid that is not walked.

        Args:
            kid: The kid.
            frame: Its holder's frame.

        Returns:
            True if the holder keeps kid.
        """
        raise NotImplementedError

    def _leave(self, frame: _KidsFrame) -> bool:
        """Finish a node other than the root, whose kids are all decided.

        Args:
            frame: The node's frame.

        Returns:
            True if the node's parent keeps it.
        """
        raise NotImplementedError


class _StructTreePruner(_TreePruner):
    """Walks a structure tree, dropping what belongs to removed pages or pruned links."""

    def prune(self, root: Dictionary) -> None:
        """Prune the tree under root.

        Args:
            root: The structure tree root.
        """
        self._prune(_kids_frame(root, Name.K))

    def _enter(self, kid: Object | int) -> _KidsFrame | None:
        # Only an element not yet decided is walked; _keep() decides the
        # rest where they stand.
        if not isinstance(kid, Dictionary) or kid.get(Name.Type) in (Name.MCR, Name.OBJR):
            return None
        if not self._first_visit(kid):
            return None
        frame = _kids_frame(kid, Name.K, kid.get(Name.Pg))
        # An element with no kids is kept without being walked, so it loses
        # a /Pg naming a removed page here rather than in _leave().
        if frame is None and self._resolver.is_removed(kid.get(Name.Pg)):
            del kid.Pg
        return frame

    def _leave(self, frame: _KidsFrame) -> bool:
        holder = frame.holder
        if not frame.kept:
            self._drop(holder)
            # A dropped element can still be reached, from a /ParentTree
            # entry for a form XObject, say, so it lets go of what named
            # the removed pages.
            for key in (Name.K, Name.Pg):
                if key in holder:
                    del holder[key]
            return False
        _set_kids(frame)
        # Whatever named this page through the element has just gone, and
        # the /Pg would otherwise keep the page in the file.
        if self._resolver.is_removed(frame.page):
            del holder.Pg
        return True

    def _keep(self, kid: Object | int, frame: _KidsFrame) -> bool:
        page = frame.page
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
        return self._was_kept(kid)

    def _keep_object_reference(self, objr: Dictionary, page: Object | None) -> bool:
        resolver = self._resolver
        obj = objr.get(Name.Obj)
        if resolver.went_with_removed_pages(obj) or resolver.is_stale_link(obj):
            return False
        # A placed annotation is kept whatever /Pg says, since /Pg may name a
        # page sharing it.  Anything else, such as a form XObject, has only
        # /Pg to go by.
        if not resolver.is_placed_annotation(obj) and resolver.is_removed(objr.get(Name.Pg, page)):
            return False
        if resolver.is_removed(objr.get(Name.Pg)):
            del objr.Pg
        return True


def _stale_parent_tree_keys(resolver: _Resolver) -> set[int]:
    """Find the ``/ParentTree`` keys of what remove_pages() removes.

    They are keyed by what goes: the removed pages, by ``/StructParents``,
    and the removed annotations and the links pruned from the remaining
    pages, by ``/StructParent``.

    Args:
        resolver: The resolver for this remove_pages() call.

    Returns:
        The keys whose entries are to go.
    """
    owners: list[tuple[Object, Name]] = [(page, Name.StructParents) for page in resolver.removed_pages]
    owners += [(annot, Name.StructParent) for annot in [*resolver.removed_annots, *resolver.stale_links]]
    keys: set[int] = set()
    for owner, key in owners:
        number = owner.get(key) if isinstance(owner, Dictionary) else None
        if isinstance(number, int):
            keys.add(number)
    return keys


def _names_only_dropped(value: Object, dropped: set[_ObjGen]) -> bool:
    """Say whether a ``/ParentTree`` value names dropped elements and nothing else.

    Args:
        value: An element, or an array of elements with nulls where marked
            content has no parent.
        dropped: Object identifiers of the elements dropped.

    Returns:
        True if value names at least one dropped element and no other.
    """
    elems = value.as_list() if isinstance(value, Array) else [value]
    # Elements are indirect; a null, or anything else, names none.
    named = [objgen for objgen in map(_indirect_id, elems) if objgen is not None]
    return bool(named) and all(objgen in dropped for objgen in named)


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
    pruner.prune(root)
    dropped = pruner.dropped

    stale_keys = _stale_parent_tree_keys(resolver)
    read_numbers = _read_tree(pdf, root.get(Name.ParentTree), NumberTree)
    if read_numbers is not None:
        numbers, entries = read_numbers
        # An entry naming only dropped elements goes too, whatever its key
        # belongs to: a form XObject drawn on a removed page, say.
        stale_keys |= {number for number, value in entries.items() if _names_only_dropped(value, dropped)}
        _delete_from_tree(root, Name.ParentTree, numbers, stale_keys)

    read_ids = _read_tree(pdf, root.get(Name.IDTree), NameTree) if dropped else None
    if read_ids is not None:
        ids, id_entries = read_ids
        stale_ids = [name for name, elem in id_entries.items() if _indirect_id(elem) in dropped]
        _delete_from_tree(root, Name.IDTree, ids, stale_ids)


class _FormPruner(_TreePruner):
    """Walks a form's field tree, dropping the fields whose widgets all went."""

    def prune(self, form: Dictionary) -> None:
        """Prune the field tree under form.

        Args:
            form: The ``/AcroForm`` dictionary.
        """
        self._prune(_kids_frame(form, Name.Fields))

    def _enter(self, kid: Object | int) -> _KidsFrame | None:
        if not isinstance(kid, Dictionary) or not self._first_visit(kid):
            return None
        return _kids_frame(kid, Name.Kids)

    def _leave(self, frame: _KidsFrame) -> bool:
        _set_kids(frame)
        if not frame.kept:
            self._drop(frame.holder)
            return False
        _set_button_states(frame)
        return True

    def _keep(self, kid: Object | int, frame: _KidsFrame) -> bool:
        # A widget has no kids, so it is decided here, as is a field and
        # widget in one.
        if isinstance(kid, Object) and self._resolver.went_with_removed_pages(kid):
            self._drop(kid)
            return False
        return self._was_kept(kid)


def _field_type(field: Dictionary) -> Object | None:
    """Get a field's type, ``/FT``, which it may inherit from its ancestors.

    Args:
        field: A form field.

    Returns:
        The field's type, or None if neither it nor an ancestor has one.
    """
    node: Object | None = field
    for _ in range(_MAX_FIELD_DEPTH):
        if not isinstance(node, Dictionary):
            break
        kind = node.get(Name.FT)
        if kind is not None:
            return kind
        node = node.get(Name.Parent)
    return None


def _on_states(widget: Object | int) -> set[str]:
    """List the names of the states a button widget turns its field on with.

    Args:
        widget: A widget annotation.

    Returns:
        The keys of its normal appearances, ``/AP /N``, other than ``/Off``.
    """
    appearances = widget.get(Name.AP) if isinstance(widget, Dictionary) else None
    normal = appearances.get(Name.N) if isinstance(appearances, Dictionary) else None
    return set(normal.keys()) - {"/Off"} if isinstance(normal, Dictionary) else set()


def _rename_states(widget: Object | int, renames: dict[str, str]) -> None:
    """Rename a button widget's appearance states and its current state.

    Args:
        widget: A widget annotation.
        renames: New names by old, all applied at once.
    """
    if not isinstance(widget, Dictionary):
        return
    appearances = widget.get(Name.AP)
    if isinstance(appearances, Dictionary):
        for kind in (Name.N, Name.R, Name.D):
            states = appearances.get(kind)
            if isinstance(states, Dictionary):
                appearances[kind] = Dictionary({renames.get(key, key): value for key, value in states.items()})
    state = widget.get(Name.AS)
    if state is not None and str(state) in renames:
        widget.AS = Name(renames[str(state)])


def _set_button_states(frame: _KidsFrame) -> None:
    """Keep a button field's states in step with the widgets it kept.

    A check box or radio button field's ``/Opt`` holds one export value per
    kid, matched by position, so pruning ``/Kids`` alone would shift every
    later widget onto another's value.  ``/Opt`` loses the dropped widgets'
    entries, and on states named by position, ``/0``, ``/1`` and so on, are
    renumbered to match, in the kept widgets and in the field's ``/V`` and
    ``/DV``.  A value that only a dropped widget turned on becomes
    ``/Off``, since no widget left can show it.  ``/Opt`` of any other
    length, or of a choice field, where it lists the options, is left as
    it is.

    Args:
        frame: A field's frame, with its kids all decided.
    """
    field, items = frame.holder, frame.items
    if len(frame.kept) == len(items) or _field_type(field) != Name.Btn:
        return
    positions = frame.positions

    renames: dict[str, str] = {}
    options = field.get(Name.Opt)
    if isinstance(options, Array) and len(options) == len(items):
        field.Opt = Array([options[index] for index in positions])
        # A positional state is renamed as a whole, to the new position of
        # the first kept widget showing it, so widgets that share one, as
        # radio buttons in unison with the same export value do, still do.
        for new, kid in enumerate(frame.kept):
            for state in sorted(_on_states(kid)):
                if state[1:].isdigit():
                    renames.setdefault(state, f"/{new}")
        renames = {old: new for old, new in renames.items() if old != new}

    kept_positions = set(positions)
    dropped = [kid for index, kid in enumerate(items) if index not in kept_positions]
    kept_states = set[str]().union(*map(_on_states, frame.kept))
    dropped_states = set[str]().union(*map(_on_states, dropped))
    for widget in frame.kept:
        _rename_states(widget, renames)
    for key in (Name.V, Name.DV):
        value = field.get(key)
        if value is None:
            continue
        if str(value) in dropped_states - kept_states:
            field[key] = Name.Off
        elif str(value) in renames:
            field[key] = Name(renames[str(value)])


def _prune_form(resolver: _Resolver) -> None:
    """Drop the form fields whose widgets all went with the removed pages.

    A widget on a removed page leaves its field's ``/Kids``, or ``/Fields``
    if it is a field and widget in one, and a field left with no kids goes
    too, and so on up the tree, value and all: it appears on no page.  The
    calculation order ``/CO`` loses the fields dropped.  A form left with no
    fields stays, with the rest of ``/AcroForm`` as it was.

    Args:
        resolver: The resolver for this remove_pages() call.
    """
    form = resolver.pdf.Root.get(Name.AcroForm)
    if not isinstance(form, Dictionary):
        return
    pruner = _FormPruner(resolver)
    pruner.prune(form)

    order = form.get(Name.CO)
    if isinstance(order, Array):
        dropped = pruner.dropped
        kept = [field for field in order.as_list() if _indirect_id(field) not in dropped]
        if len(kept) < len(order):
            form.CO = Array(kept)


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
    the removed pages and those links.  An annotation a removed page shared
    with a remaining one names the first remaining page holding it as its
    ``/P``, and the interactive form loses the fields whose widgets were all
    on removed pages.  Article threads are left as they are, and a file
    using them keeps the pages they name in the saved file.

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
    _repoint_annotations(resolver)
    _prune_destinations(resolver)
    _prune_struct_tree(resolver)
    _prune_form(resolver)

    # A file with no labels to begin with, or labels we cannot read, is left
    # with whatever it had: there is nothing to line back up with the pages.
    if labels:
        _set_page_labels(pdf, [label for number, label in enumerate(labels, start=1) if number not in pages])
