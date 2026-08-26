"""Tests for pagewielder.core."""

import io
import unittest
from collections.abc import Sequence

import pikepdf
from pikepdf import Array, Dictionary, Name, NameTree, OutlineItem, String

from pagewielder import core
from tests.helpers import A4, PLATE, make_pdf, outline_titles, page_label_ranges, set_page_labels


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

    def test_prunes_an_action_whose_destination_is_a_name(self) -> None:
        """A GoTo action naming a destination resolves through both hops."""
        with make_pdf([A4, PLATE]) as pdf:
            name_tree = NameTree.new(pdf)
            name_tree["plate"] = Array([pdf.pages[1].obj, Name.Fit])
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=name_tree.obj))
            pdf.Root.OpenAction = Dictionary(S=Name.GoTo, D=String("plate"))

            core.remove_pages(pdf, {2})

            self.assertFalse(Name.OpenAction in pdf.Root)

    def test_tolerates_a_direct_destination_name_tree(self) -> None:
        """A name tree whose root is a direct object is left alone, not fatal."""
        with make_pdf([A4, PLATE]) as pdf:
            dests = Dictionary(Names=Array([String("plate"), Array([pdf.pages[1].obj, Name.Fit])]))
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=dests))

            core.remove_pages(pdf, {2})

            self.assertEqual(1, len(pdf.pages))

    def test_remaps_page_labels(self) -> None:
        """Labels follow the pages they describe."""
        with make_pdf([A4, A4, A4, A4]) as pdf:
            # pages 1-2 roman (i, ii), pages 3-4 arabic (1, 2)
            set_page_labels(pdf, [0, Dictionary(S=Name.r), 2, Dictionary(S=Name.D, St=1)])

            core.remove_pages(pdf, {1, 4})

            self.assertEqual([(0, {"/S": "/r", "/St": "2"}), (1, {"/S": "/D"})], page_label_ranges(pdf))

    def test_merges_page_label_ranges_that_run_on(self) -> None:
        """Pages whose labels still run on are written as one range."""
        with make_pdf([A4, A4, A4]) as pdf:
            set_page_labels(pdf, [0, Dictionary(S=Name.D), 1, Dictionary(S=Name.D, St=2)])

            core.remove_pages(pdf, {3})

            self.assertEqual([(0, {"/S": "/D"})], page_label_ranges(pdf))

    def test_keeps_page_label_prefixes(self) -> None:
        """Prefixes are carried over, and a prefix-only range stays one range."""
        with make_pdf([A4, A4, A4]) as pdf:
            set_page_labels(pdf, [0, Dictionary(P=String("cover")), 1, Dictionary(S=Name.D, P=String("A-"))])

            core.remove_pages(pdf, {2})

            self.assertEqual(
                [(0, {"/P": "cover"}), (1, {"/S": "/D", "/P": "A-", "/St": "2"})],
                page_label_ranges(pdf),
            )

    def test_drops_page_labels_when_no_labelled_page_remains(self) -> None:
        """A tree left with nothing to say is deleted."""
        with make_pdf([A4, A4]) as pdf:
            set_page_labels(pdf, [1, Dictionary(S=Name.D)])

            core.remove_pages(pdf, {2})

            self.assertFalse(Name.PageLabels in pdf.Root)

    def test_tolerates_a_malformed_page_labels_tree(self) -> None:
        """A /PageLabels tree that cannot be read is left alone, not fatal."""
        malformed: list[Sequence[int | Dictionary]] = [[0], [Dictionary(S=Name.D), 0]]
        for nums in malformed:
            with self.subTest(nums=nums):
                with make_pdf([A4, A4, PLATE]) as pdf:
                    set_page_labels(pdf, nums)

                    core.remove_pages(pdf, {3})

                    self.assertEqual(2, len(pdf.pages))

    def test_works_without_page_labels(self) -> None:
        """PDFs without /PageLabels are handled."""
        with make_pdf([A4, PLATE]) as pdf:
            core.remove_pages(pdf, {2})
            self.assertFalse(Name.PageLabels in pdf.Root)

    def test_remaps_a_direct_page_labels_dictionary(self) -> None:
        """A /PageLabels dictionary that is not an indirect object is remapped."""
        with make_pdf([A4, A4]) as pdf:
            pdf.Root.PageLabels = Dictionary(Nums=Array([0, Dictionary(S=Name.r)]))

            core.remove_pages(pdf, {1})

            self.assertEqual([(0, {"/S": "/r", "/St": "2"})], page_label_ranges(pdf))

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
