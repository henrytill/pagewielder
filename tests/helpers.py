"""Shared helpers for the pagewielder tests."""

from collections.abc import Mapping, Sequence

from pikepdf import Array, Dictionary, Name, NameTree, NumberTree, Object, Pdf

A4 = (595.0, 842.0)
PLATE = (1000.0, 700.0)


def make_pdf(sizes: list[tuple[float, float]]) -> Pdf:
    """Build a PDF with one blank page per given page size."""
    pdf = Pdf.new()
    for size in sizes:
        pdf.add_blank_page(page_size=size)
    return pdf


def outline_titles(pdf: Pdf) -> list[str]:
    """List the titles of the top-level outline items of a PDF."""
    with pdf.open_outline() as outline:
        return [item.title for item in outline.root]


def link(pdf: Pdf, dest: Object | None = None, action: Object | None = None) -> Object:
    """Build an indirect link annotation targeting a destination or an action."""
    annot = Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, 0, 10, 10]))
    if dest is not None:
        annot.Dest = dest
    if action is not None:
        annot.A = action
    return pdf.make_indirect(annot)


def set_annotations(pdf: Pdf, index: int, annots: Sequence[Object]) -> None:
    """Give the page at a 0-based index an indirect /Annots array."""
    pdf.pages[index].Annots = pdf.make_indirect(Array(annots))


def count_page_objects(pdf: Pdf) -> int:
    """Count the /Page objects in a PDF, whether or not the page tree holds them."""
    return len([o for o in pdf.objects if isinstance(o, Dictionary) and o.get(Name.Type) == Name.Page])


def annotation_ids(pdf: Pdf, index: int) -> list[tuple[int, int]]:
    """List the object identifiers of the annotations on the page at a 0-based index."""
    return [annot.objgen for annot in pdf.pages[index].Annots.as_list()]


def set_named_destinations(pdf: Pdf, dests: Mapping[str, Object]) -> None:
    """Give a PDF a /Root /Names /Dests name tree holding the given destinations."""
    tree = NameTree.new(pdf)
    for name, dest in dests.items():
        tree[name] = dest
    pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=tree.obj))


def named_destinations(pdf: Pdf) -> list[str]:
    """List the names in a PDF's /Root /Names /Dests name tree."""
    return [str(name) for name in NameTree(pdf.Root.Names.Dests).keys()]


def set_page_labels(pdf: Pdf, nums: Sequence[int | Dictionary]) -> None:
    """Give a PDF a /PageLabels number tree from a flat /Nums list."""
    pdf.Root.PageLabels = pdf.make_indirect(Dictionary(Nums=Array(nums)))


def page_label_ranges(pdf: Pdf) -> list[tuple[int, dict[str, str]]]:
    """List a PDF's /PageLabels ranges as plain Python values."""
    return [
        (index, {str(key): str(value) for key, value in entry.items()})
        for index, entry in NumberTree(pdf.Root.PageLabels).items()
    ]
