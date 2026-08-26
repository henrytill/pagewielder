"""Tests for pagewielder.core."""

import io
import unittest

import pikepdf
from pikepdf import Array, Dictionary, Name, NameTree, OutlineItem, String

from pagewielder import core
from tests.helpers import A4, PLATE, make_pdf, outline_titles


class MapDimensionsToPagesTest(unittest.TestCase):
    """Tests for map_dimensions_to_pages."""

    def test_groups_pages_by_dimensions(self) -> None:
        """Pages are grouped by their dimensions."""
        with make_pdf([A4, A4, PLATE, A4]) as pdf:
            mapping = core.map_dimensions_to_pages(pdf)
        self.assertEqual({A4: {1, 2, 4}, PLATE: {3}}, mapping)


class RemovePagesTest(unittest.TestCase):
    """Tests for remove_pages."""

    def test_removes_pages(self) -> None:
        """The given pages are removed."""
        with make_pdf([A4, A4, PLATE, A4]) as pdf:
            core.remove_pages(pdf, {3})
            self.assertEqual(3, len(pdf.pages))
            self.assertEqual({A4: {1, 2, 3}}, core.map_dimensions_to_pages(pdf))

    def test_works_without_outline(self) -> None:
        """PDFs without an outline are handled."""
        with make_pdf([A4, PLATE]) as pdf:
            core.remove_pages(pdf, {2})
            self.assertEqual(1, len(pdf.pages))
            self.assertFalse(Name.Outlines in pdf.Root)

    def test_preserves_outline_for_remaining_pages(self) -> None:
        """Outline entries for remaining pages survive."""
        with make_pdf([A4, A4, PLATE, A4]) as pdf:
            with pdf.open_outline() as outline:
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(OutlineItem("Chapter 2", 1))
                outline.root.append(OutlineItem("Chapter 3", 3))

            core.remove_pages(pdf, {3})

            self.assertEqual(["Chapter 1", "Chapter 2", "Chapter 3"], outline_titles(pdf))
            with pdf.open_outline() as outline:
                last = outline.root[-1].destination
                assert isinstance(last, Array)
                self.assertEqual(pdf.pages[2].obj.objgen, last[0].objgen)

    def test_prunes_entries_for_removed_pages_and_promotes_children(self) -> None:
        """Entries for removed pages are dropped and their children promoted."""
        with make_pdf([A4, A4, PLATE, A4]) as pdf:
            with pdf.open_outline() as outline:
                plate = OutlineItem("Plate", 2)
                plate.children.append(OutlineItem("Detail", 3))
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(plate)

            core.remove_pages(pdf, {3})

            self.assertEqual(["Chapter 1", "Detail"], outline_titles(pdf))

    def test_prunes_entries_using_goto_actions(self) -> None:
        """Entries using GoTo actions are pruned."""
        with make_pdf([A4, PLATE]) as pdf:
            with pdf.open_outline() as outline:
                action = Dictionary(S=Name.GoTo, D=Array([pdf.pages[1].obj, Name.Fit]))
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(OutlineItem("Plate", action=action))

            core.remove_pages(pdf, {2})

            self.assertEqual(["Chapter 1"], outline_titles(pdf))

    def test_prunes_entries_using_named_destinations(self) -> None:
        """Entries using named destinations are pruned."""
        with make_pdf([A4, PLATE]) as pdf:
            name_tree = NameTree.new(pdf)
            name_tree["plate"] = Array([pdf.pages[1].obj, Name.Fit])
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=name_tree.obj))
            with pdf.open_outline() as outline:
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(OutlineItem("Plate", String("plate")))

            core.remove_pages(pdf, {2})

            self.assertEqual(["Chapter 1"], outline_titles(pdf))

    def test_prunes_named_destinations_for_removed_pages(self) -> None:
        """Named destinations pointing at removed pages are dropped."""
        buffer = io.BytesIO()
        with make_pdf([A4, PLATE]) as pdf:
            name_tree = NameTree.new(pdf)
            name_tree["plate"] = Array([pdf.pages[1].obj, Name.Fit])
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=name_tree.obj))
            pdf.Root.OpenAction = Array([pdf.pages[1].obj, Name.Fit])

            core.remove_pages(pdf, {2})

            self.assertEqual([], list(NameTree(pdf.Root.Names.Dests).keys()))
            self.assertFalse(Name.OpenAction in pdf.Root)
            pdf.save(buffer)

        buffer.seek(0)
        with pikepdf.open(buffer) as reloaded:
            # The removed page is gone from the file, not merely unlinked.
            self.assertEqual(1, len([o for o in reloaded.objects if o.get(Name.Type) == Name.Page]))

    def test_tolerates_a_direct_destination_name_tree(self) -> None:
        """A name tree whose root is a direct object is left alone, not fatal."""
        with make_pdf([A4, PLATE]) as pdf:
            dests = Dictionary(Names=Array([String("plate"), Array([pdf.pages[1].obj, Name.Fit])]))
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=dests))

            core.remove_pages(pdf, {2})

            self.assertEqual(1, len(pdf.pages))

    def test_outline_survives_save_and_reload(self) -> None:
        """The pruned outline survives a save/reload round trip."""
        buffer = io.BytesIO()
        with make_pdf([A4, A4, PLATE]) as pdf:
            with pdf.open_outline() as outline:
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(OutlineItem("Chapter 2", 1))
            core.remove_pages(pdf, {3})
            pdf.save(buffer)

        buffer.seek(0)
        with pikepdf.open(buffer) as reloaded:
            self.assertEqual(2, len(reloaded.pages))
            self.assertEqual(["Chapter 1", "Chapter 2"], outline_titles(reloaded))


if __name__ == "__main__":
    unittest.main()
