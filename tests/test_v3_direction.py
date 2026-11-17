"""Direction evidence must stop at the next equipment pair, crossing or contradiction."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, ImageDraw

from aip.v3.vision import _check_vision_routes, extract_local_connections


def drawing(*, reverse=False, vertical=False, branch=None, gap=False, opposing=False, on_edge=False):
    image = Image.new("L", (260, 100), "white")
    draw = ImageDraw.Draw(image)
    components = [{"id": str(i), "type": "valve", "bbox": [x, 32, x + 20, 52]}
                  for i, x in enumerate((30, 90, 150))]
    for node in components:
        l, t, r, b = node["bbox"]
        draw.rectangle((l, t, r - 1, b - 1), outline="black")
    draw.line((10, 42, 218, 42), fill="black")
    heads = [(125 if on_edge else 218, 1)]
    if opposing:
        heads.append((10, -1))
    for tip, sign in heads:
        rear = tip - 11 * sign
        draw.line([(rear, 35), (tip, 42), (rear, 49)], fill="black")
    if gap:
        draw.line((183, 42, 190, 42), fill="white")
    if branch is not None:
        draw.line((branch, 42, branch, 63), fill="black")
    if reverse:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        for node in components:
            l, t, r, b = node["bbox"]
            node["bbox"] = [image.width - r, t, image.width - l, b]
    if vertical:
        image = image.transpose(Image.Transpose.TRANSPOSE)
        for node in components:
            l, t, r, b = node["bbox"]
            node["bbox"] = [t, l, b, r]
    return image, components


def trace(**kwargs):
    image, components = drawing(**kwargs)
    with TemporaryDirectory() as scratch:
        path = Path(scratch) / "test.png"
        image.save(path)
        return extract_local_connections(path, components)


class DirectionTests(unittest.TestCase):
    def test_terminal_tip_orients_only_immediate_pairs_in_all_four_headings(self):
        for reverse in (False, True):
            for vertical in (False, True):
                with self.subTest(reverse=reverse, vertical=vertical):
                    result = trace(reverse=reverse, vertical=vertical)
                    self.assertEqual([(e["source"], e["target"]) for e in result["connections"]],
                                     [("0", "1"), ("1", "2")])
                    self.assertEqual(len(result["arrows"]), 1)
                    head = result["arrows"][0]
                    self.assertTrue(head["verified"])
                    self.assertNotIn("connection_id", head)
                    for edge in result["connections"]:
                        self.assertEqual(edge["direction"], "source_to_target")
                        self.assertEqual(edge["direction_evidence"], {"basis": "run", "arrow_ids": [head["id"]]})

    def test_gap_or_branch_on_terminal_stem_cannot_orient_other_equipment(self):
        for kwargs in ({"branch": 185}, {"gap": True}):
            with self.subTest(kwargs=kwargs):
                self.assertTrue(all(e["direction"] == "unknown" for e in trace(**kwargs)["connections"]))

    def test_crossing_between_equipment_stops_upstream_inference(self):
        result = trace(branch=70)
        self.assertEqual([e["direction"] for e in result["connections"]], ["unknown", "source_to_target"])

    def test_conflicting_terminal_arrows_are_retained_without_claiming_flow(self):
        result = trace(opposing=True)
        self.assertEqual(len(result["arrows"]), 2)
        for edge in result["connections"]:
            self.assertEqual(edge["direction"], "unknown")
            self.assertEqual(edge["direction_evidence"]["basis"], "conflict")

    def test_head_on_direct_edge_and_on_run_have_distinct_evidence(self):
        result = trace(on_edge=True)
        self.assertEqual([e["direction_evidence"]["basis"] for e in result["connections"]], ["run", "on_edge"])
        self.assertEqual(result["arrows"][0]["connection_id"], result["connections"][1]["id"])

    def test_on_edge_head_does_not_assign_flow_across_an_intervening_branch(self):
        for vertical in (False, True):
            result = trace(on_edge=True, branch=138, vertical=vertical)
            self.assertEqual(result["connections"][1]["direction"], "unknown")

    def test_hosted_terminal_observation_uses_only_verified_adjacent_edges(self):
        image, components = drawing()
        ink = image.point(lambda v: 255 if v < 160 else 0)
        graph = {"components": components, "uncertainties": [], "connections": [
            {"id": "e0", "source": "1", "target": "0", "kind": "pipe", "path": [[90, 42], [49, 42]],
             "evidence": "Continuous line.", "direction": "unknown"},
            {"id": "e1", "source": "1", "target": "2", "kind": "pipe", "path": [[109, 42], [150, 42]],
             "evidence": "Continuous line.", "direction": "unknown"}],
            "arrows": [{"id": "tip", "bbox": [207, 35, 219, 50], "tail": [207, 42], "tip": [218, 42]}]}
        result = _check_vision_routes(graph, ink)
        self.assertEqual([edge["direction"] for edge in result["connections"]], ["target_to_source", "source_to_target"])
        self.assertTrue(result["arrows"][0]["verified"])
        self.assertNotIn("connection_id", result["arrows"][0])
