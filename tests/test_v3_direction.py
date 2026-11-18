"""Direction evidence must stop at the next equipment pair, crossing or contradiction."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from PIL import Image, ImageDraw

from aip.v3.vision import _check_vision_routes, extract_local_connections, refresh_trace_directions


def drawing(*, reverse=False, vertical=False, branch=None, gap=False, opposing=False,
            on_edge=False, tip_at=None, extra_port=None):
    image = Image.new("L", (260, 100), "white")
    draw = ImageDraw.Draw(image)
    components = [{"id": str(i), "type": "valve", "bbox": [x, 32, x + 20, 52]}
                  for i, x in enumerate((30, 90, 150))]
    for node in components:
        l, t, r, b = node["bbox"]
        draw.rectangle((l, t, r - 1, b - 1), outline="black")
    draw.line((10, 42, 218, 42), fill="black")
    heads = [(tip_at if tip_at is not None else 125 if on_edge else 218, 1)]
    if opposing:
        heads.append((10, -1))
    for tip, sign in heads:
        rear = tip - 11 * sign
        draw.line([(rear, 35), (tip, 42), (rear, 49)], fill="black")
    if gap:
        draw.line((183, 42, 190, 42), fill="white")
    if branch is not None:
        draw.line((branch, 42, branch, 63), fill="black")
    if extra_port == "detached":
        draw.line((96, 51, 104, 51), fill="white")
        draw.line((100, 52, 100, 72), fill="black")
    elif extra_port == "connected":
        draw.line((100, 42, 100, 72), fill="black")
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
    def test_offset_inline_ports_carry_arrow_evidence_through_only_visible_ink(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), TemporaryDirectory() as scratch:
                image = Image.new("L", (340, 140), "white")
                draw = ImageDraw.Draw(image)
                boxes = [[22, 43, 50, 73], [110, 42, 141, 87], [231, 58, 261, 86]]
                draw.rectangle((22, 43, 49, 72), outline="black")
                draw.rectangle((231, 58, 260, 85), outline="black")
                draw.line((40, 55, 120, 55), fill="black", width=2)
                draw.line((129, 70, 296, 70), fill="black", width=2)
                if not interrupted:
                    draw.line((120, 55, 129, 70), fill="black", width=2)
                draw.line(((284, 62), (296, 70), (284, 78)), fill="black")
                path = Path(scratch) / "drawing.png"
                image.save(path)
                nodes = [{"id": str(i), "type": "valve", "bbox": box} for i, box in enumerate(boxes)]
                result = extract_local_connections(path, nodes)
                self.assertEqual([("0", "1"), ("1", "2")],
                                 [(edge["source"], edge["target"]) for edge in result["connections"]])
                self.assertEqual([("unknown" if interrupted else "source_to_target"), "source_to_target"],
                                 [edge["direction"] for edge in result["connections"]])

    def test_hosted_direction_finds_a_printed_arrow_along_shallow_diagonal_coordinates(self):
        image = Image.new("L", (340, 210))
        draw = ImageDraw.Draw(image)
        draw.line((30, 66, 300, 156), fill=255, width=4)
        draw.polygon(((174, 107), (214, 127), (170, 126)), fill=255)
        trace = {"components": [
                    {"id": "left", "bbox": [20, 55, 37, 83], "type": "valve"},
                    {"id": "right", "bbox": [291, 145, 312, 168], "type": "valve"},
                 ],
                 "connections": [{"id": "only", "source": "left", "target": "right",
                                  "kind": "pipe", "path": [[36, 68], [292, 153]],
                                  "evidence": "Continuous stroke.", "direction": "unknown"}],
                 "arrows": [], "uncertainties": []}
        result = _check_vision_routes(trace, image)
        self.assertEqual("source_to_target", result["connections"][0]["direction"])
        self.assertEqual("on_edge", result["connections"][0]["direction_evidence"]["basis"])
        self.assertEqual("only", result["arrows"][0]["connection_id"])

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

    def test_run_ignores_detached_ink_but_stops_at_connected_symbol_port(self):
        detached = trace(extra_port="detached")
        self.assertEqual([e["direction"] for e in detached["connections"]],
                         ["source_to_target", "source_to_target"])
        connected = trace(extra_port="connected")
        self.assertEqual([e["direction"] for e in connected["connections"]],
                         ["unknown", "source_to_target"])

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

    def test_arrow_orients_the_continuous_downstream_run(self):
        for reverse, vertical in ((False, False), (True, True)):
            with self.subTest(reverse=reverse, vertical=vertical):
                result = trace(tip_at=72, reverse=reverse, vertical=vertical)
                self.assertEqual([edge["direction"] for edge in result["connections"]],
                                 ["source_to_target", "source_to_target"])
                self.assertEqual([edge["direction_evidence"]["basis"] for edge in result["connections"]],
                                 ["on_edge", "run"])
        result = trace(tip_at=72, branch=126)
        self.assertEqual([edge["direction"] for edge in result["connections"]], ["source_to_target", "unknown"])

    def test_saved_direction_recheck_uses_original_pixels_without_changing_trace(self):
        image, components = drawing(tip_at=72)
        with TemporaryDirectory() as scratch:
            path = Path(scratch) / "test.png"
            image.save(path)
            saved = extract_local_connections(path, components)
            saved.update(created_at="saved")
            saved["connections"][1].update(direction="unknown")
            saved["connections"][1].pop("direction_evidence")
            with patch("aip.v3.vision.extract_provider") as provider:
                checked = refresh_trace_directions(saved, path.read_bytes())
                provider.assert_not_called()
            with self.assertRaisesRegex(ValueError, "different drawing"):
                refresh_trace_directions(saved, b"mismatched PNG")
        self.assertEqual(checked["created_at"], "saved")
        self.assertEqual(checked["components"], saved["components"])
        self.assertEqual([e["id"] for e in checked["connections"]], [e["id"] for e in saved["connections"]])
        self.assertEqual(checked["connections"][1]["direction"], "source_to_target")
        self.assertEqual(saved["connections"][1]["direction"], "unknown")

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
