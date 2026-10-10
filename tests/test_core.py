"""Tests for pagewielder.core."""

import io
import sys
import unittest
from collections.abc import Sequence

import pikepdf
from pikepdf import Array, Dictionary, Name, NameTree, OutlineItem, Stream, String

from pagewielder import core
from tests.helpers import (
    A4,
    PLATE,
    annotation_ids,
    field,
    kid_ids,
    link,
    make_pdf,
    named_destinations,
    outline_titles,
    page_label_ranges,
    parent_tree_keys,
    saved_page_objects,
    set_annotations,
    set_form,
    set_named_destinations,
    set_page_labels,
    set_struct_tree,
    struct_elem,
    text_annotation,
    widget,
)


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
            set_named_destinations(pdf, {"plate": Array([pdf.pages[1].obj, Name.Fit])})
            with pdf.open_outline() as outline:
                outline.root.append(OutlineItem("Chapter 1", 0))
                outline.root.append(OutlineItem("Plate", String("plate")))

            core.remove_pages(pdf, {2})

            self.assertEqual(["Chapter 1"], outline_titles(pdf))

    def test_prunes_named_destinations_for_removed_pages(self) -> None:
        """Named destinations pointing at removed pages are dropped."""
        with make_pdf([A4, PLATE]) as pdf:
            set_named_destinations(pdf, {"plate": Array([pdf.pages[1].obj, Name.Fit])})
            pdf.Root.OpenAction = Array([pdf.pages[1].obj, Name.Fit])

            core.remove_pages(pdf, {2})

            self.assertEqual([], named_destinations(pdf))
            self.assertFalse(Name.OpenAction in pdf.Root)
            # The removed page is gone from the file, not merely unlinked.
            self.assertEqual(1, saved_page_objects(pdf))

    def test_prunes_an_action_whose_destination_is_a_name(self) -> None:
        """A GoTo action naming a destination resolves through both hops."""
        with make_pdf([A4, PLATE]) as pdf:
            set_named_destinations(pdf, {"plate": Array([pdf.pages[1].obj, Name.Fit])})
            pdf.Root.OpenAction = Dictionary(S=Name.GoTo, D=String("plate"))

            core.remove_pages(pdf, {2})

            self.assertFalse(Name.OpenAction in pdf.Root)

    def test_keeps_a_remote_open_action(self) -> None:
        """A GoToR open action names a destination in another file, not this one."""
        with make_pdf([A4, PLATE]) as pdf:
            set_named_destinations(pdf, {"plate": Array([pdf.pages[1].obj, Name.Fit])})
            pdf.Root.OpenAction = Dictionary(S=Name.GoToR, F=String("other.pdf"), D=String("plate"))

            core.remove_pages(pdf, {2})

            self.assertTrue(Name.OpenAction in pdf.Root)

    def test_prunes_links_to_removed_pages(self) -> None:
        """Links pointing at a removed page are deleted, and the page with them."""
        with make_pdf([A4, PLATE]) as pdf:
            set_annotations(pdf, 0, [link(pdf, dest=Array([pdf.pages[1].obj, Name.Fit]))])

            core.remove_pages(pdf, {2})

            self.assertEqual([], annotation_ids(pdf, 0))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_prunes_links_using_goto_actions_and_named_destinations(self) -> None:
        """A link whose GoTo action names a destination resolves through both hops."""
        with make_pdf([A4, PLATE]) as pdf:
            set_named_destinations(pdf, {"plate": Array([pdf.pages[1].obj, Name.Fit])})
            set_annotations(pdf, 0, [link(pdf, action=Dictionary(S=Name.GoTo, D=String("plate")))])

            core.remove_pages(pdf, {2})

            self.assertEqual([], annotation_ids(pdf, 0))

    def test_keeps_other_annotations(self) -> None:
        """Links to remaining pages, other actions and other annotations survive."""
        with make_pdf([A4, A4, PLATE]) as pdf:
            kept = [
                link(pdf, dest=Array([pdf.pages[1].obj, Name.Fit])),
                link(pdf, action=Dictionary(S=Name.URI, URI=String("https://example.com"))),
                link(pdf, action=Dictionary(S=Name.GoToR, F=String("other.pdf"), D=Array([1, Name.Fit]))),
                text_annotation(pdf),
            ]
            set_annotations(pdf, 0, [*kept, link(pdf, dest=Array([pdf.pages[2].obj, Name.Fit]))])

            core.remove_pages(pdf, {3})

            self.assertEqual([annot.objgen for annot in kept], annotation_ids(pdf, 0))

    def test_repoints_an_annotation_shared_with_a_remaining_page(self) -> None:
        """An annotation a removed page shared names the first remaining page holding it as its /P."""
        with make_pdf([A4, A4, A4]) as pdf:
            annot = text_annotation(pdf, page=pdf.pages[0].obj)
            set_annotations(pdf, 0, [annot])
            pdf.pages[1].Annots = pdf.pages[0].Annots
            set_annotations(pdf, 2, [annot])

            core.remove_pages(pdf, {1})

            self.assertEqual(pdf.pages[0].objgen, annot.P.objgen)
            self.assertEqual(2, saved_page_objects(pdf))

    def test_prunes_links_in_a_shared_annotations_array(self) -> None:
        """An /Annots array shared between remaining pages is pruned once, correctly."""
        with make_pdf([A4, A4, PLATE]) as pdf:
            kept = link(pdf, dest=Array([pdf.pages[0].obj, Name.Fit]))
            set_annotations(pdf, 0, [kept, link(pdf, dest=Array([pdf.pages[2].obj, Name.Fit]))])
            pdf.pages[1].Annots = pdf.pages[0].Annots

            core.remove_pages(pdf, {3})

            self.assertEqual([kept.objgen], annotation_ids(pdf, 0))
            self.assertEqual([kept.objgen], annotation_ids(pdf, 1))

    def test_prunes_a_direct_destination_name_tree(self) -> None:
        """A name tree whose root is a direct object is pruned like any other."""
        with make_pdf([A4, PLATE]) as pdf:
            dests = Dictionary(Names=Array([String("plate"), Array([pdf.pages[1].obj, Name.Fit])]))
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=dests))

            core.remove_pages(pdf, {2})

            self.assertEqual([], named_destinations(pdf))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_leaves_a_direct_destination_name_tree_with_nothing_to_prune(self) -> None:
        """A direct name tree naming only remaining pages is not made indirect."""
        with make_pdf([A4, PLATE]) as pdf:
            dests = Dictionary(Names=Array([String("first"), Array([pdf.pages[0].obj, Name.Fit])]))
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=dests))
            set_annotations(pdf, 0, [link(pdf, dest=String("first"))])

            core.remove_pages(pdf, {2})

            self.assertFalse(pdf.Root.Names.Dests.is_indirect)
            self.assertEqual(1, len(annotation_ids(pdf, 0)))

    def test_tolerates_an_unsorted_destination_name_tree(self) -> None:
        """A name tree with its names out of order does not stop the removal."""
        with make_pdf([A4, PLATE]) as pdf:
            dests = Dictionary(
                Names=Array(
                    [
                        String("plate"),
                        Array([pdf.pages[1].obj, Name.Fit]),
                        String("first"),
                        Array([pdf.pages[0].obj, Name.Fit]),
                    ]
                )
            )
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=pdf.make_indirect(dests)))

            core.remove_pages(pdf, {2})

            self.assertEqual(1, len(pdf.pages))

    def test_tolerates_a_malformed_destination_name_tree(self) -> None:
        """A /Names /Dests tree that cannot be read is left alone, and the rest is still pruned."""
        with make_pdf([A4, A4]) as pdf:
            malformed = pdf.make_indirect(Dictionary(Names=Array([Dictionary(), String("x")])))
            pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=malformed))
            with pdf.open_outline() as outline:
                outline.root.append(OutlineItem("Kept", 0))
                outline.root.append(OutlineItem("Dropped", 1))

            core.remove_pages(pdf, {2})

            self.assertEqual(1, len(pdf.pages))
            self.assertEqual(["Kept"], outline_titles(pdf))
            self.assertEqual(malformed.objgen, pdf.Root.Names.Dests.objgen)

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

    def test_keeps_a_page_label_start_written_as_a_real(self) -> None:
        """An out-of-spec /St written as a real still numbers its range."""
        with make_pdf([A4, A4, A4]) as pdf:
            set_page_labels(pdf, [0, Dictionary(S=Name.D, St=3.0)])

            core.remove_pages(pdf, {1})

            self.assertEqual([(0, {"/S": "/D", "/St": "4"})], page_label_ranges(pdf))

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


class StructTreeTest(unittest.TestCase):
    """Tests for how remove_pages prunes the structure tree."""

    def test_drops_the_reference_to_a_pruned_link(self) -> None:
        """A pruned link leaves the structure tree, which keeps an empty root."""
        with make_pdf([A4, A4]) as pdf:
            annot = link(pdf, dest=Array([pdf.pages[1].obj, Name.Fit]))
            annot.StructParent = 0
            set_annotations(pdf, 0, [annot])
            objr = Dictionary(Type=Name.OBJR, Obj=annot, Pg=pdf.pages[0].obj)
            link_elem = struct_elem(pdf, objr, page=pdf.pages[0].obj)
            set_struct_tree(pdf, [link_elem], {0: link_elem})

            core.remove_pages(pdf, {2})

            self.assertEqual([], kid_ids(pdf.Root.StructTreeRoot, Name.K))
            self.assertEqual([], parent_tree_keys(pdf))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_drops_the_parent_tree_entry_of_a_pruned_link_with_no_reference(self) -> None:
        """A pruned link's /ParentTree entry goes even when no object reference names the link."""
        with make_pdf([A4, A4]) as pdf:
            annot = link(pdf, dest=Array([pdf.pages[1].obj, Name.Fit]))
            annot.StructParent = 0
            set_annotations(pdf, 0, [annot])
            elem = struct_elem(pdf, 0, page=pdf.pages[0].obj)
            set_struct_tree(pdf, [elem], {0: elem})

            core.remove_pages(pdf, {2})

            self.assertEqual([], parent_tree_keys(pdf))
            self.assertEqual([elem.objgen], kid_ids(pdf.Root.StructTreeRoot, Name.K))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_drops_the_reference_to_an_annotation_placed_only_by_its_page(self) -> None:
        """An annotation on a removed page leaves the tree though no /P or /Pg names the page."""
        with make_pdf([A4, A4]) as pdf:
            annot = text_annotation(pdf)
            annot.StructParent = 0
            set_annotations(pdf, 1, [annot])
            elem = struct_elem(pdf, Dictionary(Type=Name.OBJR, Obj=annot))
            set_struct_tree(pdf, [elem], {0: elem})

            core.remove_pages(pdf, {2})

            self.assertEqual([], kid_ids(pdf.Root.StructTreeRoot, Name.K))
            self.assertEqual([], parent_tree_keys(pdf))

    def test_drops_the_reference_to_an_unplaced_annotation_naming_a_removed_page(self) -> None:
        """An annotation no /Annots holds leaves the tree when its /P names a removed page."""
        with make_pdf([A4, A4]) as pdf:
            annot = text_annotation(pdf, page=pdf.pages[1].obj)
            elem = struct_elem(pdf, Dictionary(Type=Name.OBJR, Obj=annot))
            set_struct_tree(pdf, [elem], {})

            core.remove_pages(pdf, {2})

            self.assertEqual([], kid_ids(pdf.Root.StructTreeRoot, Name.K))

    def test_keeps_an_annotation_shared_with_a_remaining_page(self) -> None:
        """An annotation in an /Annots array a remaining page shares stays in the tree."""
        with make_pdf([A4, A4]) as pdf:
            annot = text_annotation(pdf, page=pdf.pages[1].obj)
            annot.StructParent = 0
            set_annotations(pdf, 1, [annot])
            pdf.pages[0].Annots = pdf.pages[1].Annots
            objr = Dictionary(Type=Name.OBJR, Obj=annot, Pg=pdf.pages[1].obj)
            elem = struct_elem(pdf, objr)
            set_struct_tree(pdf, [elem], {0: elem})

            core.remove_pages(pdf, {2})

            self.assertEqual([elem.objgen], kid_ids(pdf.Root.StructTreeRoot, Name.K))
            self.assertEqual([0], parent_tree_keys(pdf))
            self.assertFalse(Name.Pg in elem.K)

    def test_drops_elements_on_removed_pages(self) -> None:
        """Elements whose content was all on removed pages go, with their /ParentTree entries."""
        with make_pdf([A4, A4]) as pdf:
            first = struct_elem(pdf, 0, page=pdf.pages[0].obj)
            second = struct_elem(pdf, 0, page=pdf.pages[1].obj)
            document = struct_elem(pdf, [first, second])
            pdf.pages[0].StructParents = 0
            pdf.pages[1].StructParents = 1
            set_struct_tree(pdf, [document], {0: Array([first]), 1: Array([second])})

            core.remove_pages(pdf, {2})

            self.assertEqual([first.objgen], kid_ids(document, Name.K))
            self.assertEqual([0], parent_tree_keys(pdf))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_keeps_an_element_with_content_on_a_remaining_page(self) -> None:
        """An element keeps its content on remaining pages and loses a /Pg naming a removed one."""
        with make_pdf([A4, A4]) as pdf:
            mcr = Dictionary(Type=Name.MCR, Pg=pdf.pages[0].obj, MCID=0)
            elem = struct_elem(pdf, [0, mcr], page=pdf.pages[1].obj)
            set_struct_tree(pdf, [elem], {})

            core.remove_pages(pdf, {2})

            self.assertEqual([elem.objgen], kid_ids(pdf.Root.StructTreeRoot, Name.K))
            self.assertEqual([Name.MCR], [kid.Type for kid in elem.K.as_list()])
            self.assertFalse(Name.Pg in elem)
            self.assertEqual(1, saved_page_objects(pdf))

    def test_drops_an_element_still_named_by_a_form_xobject(self) -> None:
        """A dropped element named by a form XObject's /ParentTree entry keeps no removed page."""
        with make_pdf([A4, A4]) as pdf:
            form = pdf.make_indirect(Stream(pdf, b""))
            form.stream_dict = Dictionary(
                Type=Name.XObject, Subtype=Name.Form, BBox=Array([0, 0, 10, 10]), StructParents=5
            )
            pdf.pages[1].Resources = Dictionary(XObject=Dictionary(Fm0=form))
            mcr = Dictionary(Type=Name.MCR, Pg=pdf.pages[1].obj, Stm=form, MCID=0)
            elem = struct_elem(pdf, mcr, page=pdf.pages[1].obj)
            set_struct_tree(pdf, [elem], {5: Array([elem])})

            core.remove_pages(pdf, {2})

            self.assertEqual([], kid_ids(pdf.Root.StructTreeRoot, Name.K))
            self.assertEqual([], parent_tree_keys(pdf))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_prunes_a_tree_deeper_than_the_recursion_limit(self) -> None:
        """A tree nested deeper than Python's stack allows is pruned, not given up on."""
        depth = sys.getrecursionlimit() * 2
        with make_pdf([A4, A4]) as pdf:
            kept = struct_elem(pdf, 0, page=pdf.pages[0].obj)
            dropped = struct_elem(pdf, 0, page=pdf.pages[1].obj)
            for _ in range(depth):
                kept = struct_elem(pdf, kept)
                dropped = struct_elem(pdf, dropped)
            set_struct_tree(pdf, [kept, dropped], {})

            core.remove_pages(pdf, {2})

            self.assertEqual([kept.objgen], kid_ids(pdf.Root.StructTreeRoot, Name.K))

    def test_keeps_an_element_that_had_no_kids(self) -> None:
        """An element with an empty /K was not emptied by the removal, and stays."""
        with make_pdf([A4, A4]) as pdf:
            first = struct_elem(pdf, 0, page=pdf.pages[0].obj)
            figure = struct_elem(pdf, [])
            set_struct_tree(pdf, [first, figure], {})

            core.remove_pages(pdf, {2})

            self.assertEqual([first.objgen, figure.objgen], kid_ids(pdf.Root.StructTreeRoot, Name.K))

    def test_keeps_an_element_with_no_kids_off_a_removed_page(self) -> None:
        """An element with no kids keeps no /Pg naming a removed page."""
        with make_pdf([A4, A4]) as pdf:
            figure = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, Pg=pdf.pages[1].obj))
            set_struct_tree(pdf, [figure], {})

            core.remove_pages(pdf, {2})

            self.assertEqual([figure.objgen], kid_ids(pdf.Root.StructTreeRoot, Name.K))
            self.assertFalse(Name.Pg in figure)
            self.assertEqual(1, saved_page_objects(pdf))

    def test_prunes_the_id_tree(self) -> None:
        """A dropped element leaves /IDTree."""
        with make_pdf([A4, A4]) as pdf:
            first = struct_elem(pdf, 0, page=pdf.pages[0].obj)
            second = struct_elem(pdf, 0, page=pdf.pages[1].obj)
            root = set_struct_tree(pdf, [first, second], {}, ids={"first": first, "second": second})

            core.remove_pages(pdf, {2})

            self.assertEqual(["first"], list(NameTree(root.IDTree).keys()))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_tolerates_malformed_parent_and_id_trees(self) -> None:
        """A /ParentTree or /IDTree that cannot be read is left alone, not fatal."""
        with make_pdf([A4, A4]) as pdf:
            first = struct_elem(pdf, 0, page=pdf.pages[0].obj)
            second = struct_elem(pdf, 0, page=pdf.pages[1].obj)
            pdf.pages[1].StructParents = 0
            root = set_struct_tree(pdf, [first, second], {})
            root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([Dictionary(S=Name.D), 0])))
            root.IDTree = pdf.make_indirect(Dictionary(Names=Array([Dictionary(), String("second")])))

            core.remove_pages(pdf, {2})

            self.assertEqual(1, len(pdf.pages))
            self.assertEqual([first.objgen], kid_ids(root, Name.K))

    def test_tolerates_a_null_in_the_id_tree(self) -> None:
        """A null /IDTree value does not stop the removal."""
        with make_pdf([A4, A4]) as pdf:
            first = struct_elem(pdf, 0, page=pdf.pages[0].obj)
            second = struct_elem(pdf, 0, page=pdf.pages[1].obj)
            root = set_struct_tree(pdf, [first, second], {}, ids={"second": second})
            root.IDTree.Names.append(String("zzz"))
            root.IDTree.Names.append(None)

            core.remove_pages(pdf, {2})

            self.assertEqual(["zzz"], list(NameTree(root.IDTree).keys()))

    def test_prunes_a_direct_parent_tree(self) -> None:
        """A /ParentTree written as a direct dictionary is pruned too."""
        with make_pdf([A4, A4]) as pdf:
            first = struct_elem(pdf, 0, page=pdf.pages[0].obj)
            second = struct_elem(pdf, 0, page=pdf.pages[1].obj)
            pdf.pages[0].StructParents = 0
            pdf.pages[1].StructParents = 1
            root = set_struct_tree(pdf, [first, second], {0: Array([first]), 1: Array([second])})
            root.ParentTree = root.ParentTree.copy()

            core.remove_pages(pdf, {2})

            self.assertEqual([0], parent_tree_keys(pdf))
            self.assertEqual(1, saved_page_objects(pdf))


class FormTest(unittest.TestCase):
    """Tests for how remove_pages prunes the interactive form."""

    def test_drops_a_field_whose_widget_was_on_a_removed_page(self) -> None:
        """A field and widget in one on a removed page leaves /Fields; the emptied form stays."""
        with make_pdf([A4, A4]) as pdf:
            merged = widget(pdf, 1, name="gone")
            form = set_form(pdf, [merged])

            core.remove_pages(pdf, {2})

            self.assertEqual([], kid_ids(form, Name.Fields))
            self.assertTrue(Name.AcroForm in pdf.Root)
            self.assertEqual(1, saved_page_objects(pdf))

    def test_keeps_a_field_with_a_widget_on_a_remaining_page(self) -> None:
        """A field loses its widget on a removed page and keeps the one on a remaining page."""
        with make_pdf([A4, A4]) as pdf:
            kept, gone = widget(pdf, 0), widget(pdf, 1)
            node = field(pdf, "f", [kept, gone])
            form = set_form(pdf, [node])

            core.remove_pages(pdf, {2})

            self.assertEqual([node.objgen], kid_ids(form, Name.Fields))
            self.assertEqual([kept.objgen], kid_ids(node, Name.Kids))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_drops_the_export_values_of_dropped_buttons(self) -> None:
        """A button field's /Opt loses the export values of the widgets dropped, and a choice field's stays."""
        with make_pdf([A4, A4]) as pdf:
            yes, no = widget(pdf, 1), widget(pdf, 0)
            radio = field(pdf, "radio", [yes, no])
            radio.FT = Name.Btn
            radio.Opt = Array([String("Yes"), String("No")])
            choice = field(pdf, "choice", [widget(pdf, 1), widget(pdf, 0)])
            choice.FT = Name.Ch
            choice.Opt = Array([String("a"), String("b")])
            set_form(pdf, [radio, choice])

            core.remove_pages(pdf, {2})

            self.assertEqual([no.objgen], kid_ids(radio, Name.Kids))
            self.assertEqual(["No"], [str(option) for option in radio.Opt.as_list()])
            self.assertEqual(["a", "b"], [str(option) for option in choice.Opt.as_list()])

    def test_renumbers_positional_button_states(self) -> None:
        """On states named by position follow their widgets to their new positions, in /AS, /AP and /V."""
        with make_pdf([A4, A4, A4]) as pdf:
            widgets = [widget(pdf, page) for page in range(3)]
            for index, button in enumerate(widgets):
                on = Stream(pdf, b"")
                button.AP = Dictionary(N=Dictionary({f"/{index}": on, "/Off": on}))
                button.AS = Name.Off
            widgets[2].AS = Name("/2")
            radio = field(pdf, "radio", widgets)
            radio.FT = Name.Btn
            radio.Opt = Array([String("a"), String("b"), String("c")])
            radio.V = Name("/2")
            set_form(pdf, [radio])

            core.remove_pages(pdf, {1})

            self.assertEqual(["b", "c"], [str(option) for option in radio.Opt.as_list()])
            self.assertEqual({"/0", "/Off"}, set(widgets[1].AP.N.keys()))
            self.assertEqual({"/1", "/Off"}, set(widgets[2].AP.N.keys()))
            self.assertEqual(Name("/1"), widgets[2].AS)
            self.assertEqual(Name("/1"), radio.V)

    def test_keeps_buttons_in_unison_together(self) -> None:
        """Widgets sharing a positional state still share one after renumbering, and /V keeps its option."""
        with make_pdf([A4, A4, A4]) as pdf:
            widgets = [widget(pdf, page) for page in range(3)]
            for button, state in zip(widgets, ["/0", "/1", "/0"]):
                on = Stream(pdf, b"")
                button.AP = Dictionary(N=Dictionary({state: on, "/Off": on}))
            radio = field(pdf, "radio", widgets)
            radio.FT = Name.Btn
            radio.Opt = Array([String("A"), String("B"), String("A")])
            radio.V = Name("/0")
            set_form(pdf, [radio])

            core.remove_pages(pdf, {1})

            self.assertEqual(["B", "A"], [str(option) for option in radio.Opt.as_list()])
            self.assertEqual({"/0", "/Off"}, set(widgets[1].AP.N.keys()))
            self.assertEqual({"/1", "/Off"}, set(widgets[2].AP.N.keys()))
            self.assertEqual(Name("/1"), radio.V)

    def test_turns_off_a_value_only_a_dropped_button_showed(self) -> None:
        """A button field whose value only a dropped widget turned on is left off."""
        with make_pdf([A4, A4]) as pdf:
            yes, no = widget(pdf, 1), widget(pdf, 0)
            on = Stream(pdf, b"")
            yes.AP = Dictionary(N=Dictionary(Yes=on, Off=on))
            no.AP = Dictionary(N=Dictionary(No=on, Off=on))
            radio = field(pdf, "radio", [yes, no])
            radio.FT = Name.Btn
            radio.V = Name.Yes
            radio.DV = Name.No
            set_form(pdf, [radio])

            core.remove_pages(pdf, {2})

            self.assertEqual(Name.Off, radio.V)
            self.assertEqual(Name.No, radio.DV)

    def test_drops_emptied_fields_up_the_tree_and_from_the_calculation_order(self) -> None:
        """Fields left with no kids go, recursively, and leave /CO."""
        with make_pdf([A4, A4]) as pdf:
            kept = field(pdf, "kept", [widget(pdf, 0)])
            gone = field(pdf, "gone", [widget(pdf, 1)])
            parent = field(pdf, "parent", [kept, gone])
            emptied = field(pdf, "emptied", [field(pdf, "inner", [widget(pdf, 1)])])
            form = set_form(pdf, [parent, emptied], order=[kept, gone])

            core.remove_pages(pdf, {2})

            self.assertEqual([parent.objgen], kid_ids(form, Name.Fields))
            self.assertEqual([kept.objgen], kid_ids(parent, Name.Kids))
            self.assertEqual([kept.objgen], kid_ids(form, Name.CO))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_drops_a_widget_placed_only_by_its_page(self) -> None:
        """A widget no /Annots holds goes when its /P names a removed page."""
        with make_pdf([A4, A4]) as pdf:
            gone = widget(pdf, 1)
            del pdf.pages[1].Annots
            node = field(pdf, "f", [widget(pdf, 0), gone])
            set_form(pdf, [node])

            core.remove_pages(pdf, {2})

            self.assertNotIn(gone.objgen, kid_ids(node, Name.Kids))
            self.assertEqual(1, saved_page_objects(pdf))

    def test_prunes_a_field_tree_deeper_than_the_recursion_limit(self) -> None:
        """A field tree nested deeper than Python's stack allows is pruned, not given up on."""
        with make_pdf([A4, A4]) as pdf:
            node = widget(pdf, 1)
            for depth in range(sys.getrecursionlimit() * 2):
                node = field(pdf, str(depth), [node])
            form = set_form(pdf, [node])

            core.remove_pages(pdf, {2})

            self.assertEqual([], kid_ids(form, Name.Fields))


if __name__ == "__main__":
    unittest.main()
