"""Tests for pagewielder.core."""

import io
import unittest

import pikepdf
from pikepdf import Array, Dictionary, Name, NameTree, OutlineItem, Pdf, String

from pagewielder import core

A4 = (595.0, 842.0)
PLATE = (1000.0, 700.0)


def _make_pdf(sizes: list[tuple[float, float]]) -> Pdf:
    pdf = Pdf.new()
    for size in sizes:
        pdf.add_blank_page(page_size=size)
    return pdf


def _outline_titles(pdf: Pdf) -> list[str]:
    with pdf.open_outline() as outline:
        return [item.title for item in outline.root]


class MapDimensionsToPagesTest(unittest.TestCase):
    """Tests for map_dimensions_to_pages."""

    def test_groups_pages_by_dimensions(self) -> None:
        """Pages are grouped by their dimensions."""
        with _make_pdf([A4, A4, PLATE, A4]) as pdf:
            mapping = core.map_dimensions_to_pages(pdf)
        self.assertEqual({A4: {1, 2, 4}, PLATE: {3}}, mapping)


class RemovePagesTest(unittest.TestCase):
    """Tests for remove_pages."""

    def test_removes_pages(self) -> None:
        """The given pages are removed."""
        with _make_pdf([A4, A4, PLATE, A4]) as pdf:
            core.remove_pages(pdf, {3})
            self.assertEqual(3, len(pdf.pages))
            self.assertEqual({A4: {1, 2, 3}}, core.map_dimensions_to_pages(pdf))

    def test_works_without_outline(self) -> None:
        """PDFs without an outline are handled."""
        with _make_pdf([A4, PLATE]) as pdf:
            core.remove_pages(pdf, {2})
            self.assertEqual(1, len(pdf.pages))
            self.assertNotIn(Name.Outlines, pdf.Root)

    def test_preserves_outline_for_remaining_pages(self) -> None:
        """Outline entries for remaining pages survive."""
        with _make_pdf([A4, A4, PLATE, A4]) as pdf:
            with pdf.open_outline() as outline:
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(OutlineItem("Chapter 2", 1))
                outline.root.append(OutlineItem("Chapter 3", 3))

            core.remove_pages(pdf, {3})

            self.assertEqual(["Chapter 1", "Chapter 2", "Chapter 3"], _outline_titles(pdf))
            with pdf.open_outline() as outline:
                last = outline.root[-1].destination
                assert isinstance(last, Array)
                self.assertEqual(pdf.pages[2].obj.objgen, last[0].objgen)

    def test_prunes_entries_for_removed_pages_and_promotes_children(self) -> None:
        """Entries for removed pages are dropped and their children promoted."""
        with _make_pdf([A4, A4, PLATE, A4]) as pdf:
            with pdf.open_outline() as outline:
                plate = OutlineItem("Plate", 2)
                plate.children.append(OutlineItem("Detail", 3))
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(plate)

            core.remove_pages(pdf, {3})

            self.assertEqual(["Chapter 1", "Detail"], _outline_titles(pdf))

    def test_prunes_entries_using_goto_actions(self) -> None:
        """Entries using GoTo actions are pruned."""
        with _make_pdf([A4, PLATE]) as pdf:
            with pdf.open_outline() as outline:
                action = Dictionary(S=Name.GoTo, D=Array([pdf.pages[1].obj, Name.Fit]))
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(OutlineItem("Plate", action=action))

            core.remove_pages(pdf, {2})

            self.assertEqual(["Chapter 1"], _outline_titles(pdf))

    def test_prunes_entries_using_named_destinations(self) -> None:
        """Entries using named destinations are pruned."""
        with _make_pdf([A4, PLATE]) as pdf:
            name_tree = NameTree.new(pdf)
            name_tree["plate"] = Array([pdf.pages[1].obj, Name.Fit])
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=name_tree.obj))
            with pdf.open_outline() as outline:
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(OutlineItem("Plate", String("plate")))

            core.remove_pages(pdf, {2})

            self.assertEqual(["Chapter 1"], _outline_titles(pdf))

    def test_outline_survives_save_and_reload(self) -> None:
        """The pruned outline survives a save/reload round trip."""
        buffer = io.BytesIO()
        with _make_pdf([A4, A4, PLATE]) as pdf:
            with pdf.open_outline() as outline:
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(OutlineItem("Chapter 2", 1))
            core.remove_pages(pdf, {3})
            pdf.save(buffer)

        buffer.seek(0)
        with pikepdf.open(buffer) as reloaded:
            self.assertEqual(2, len(reloaded.pages))
            self.assertEqual(["Chapter 1", "Chapter 2"], _outline_titles(reloaded))


if __name__ == "__main__":
    unittest.main()
