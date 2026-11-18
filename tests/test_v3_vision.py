"""Independent constructed drawings exercise directness, missing ink, and evidence validation."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from aip.v2.providers import ProviderError, normalize_extraction
from aip.v3.line_model import classify_stroke, CONTINUOUS
from aip.v3.vision import extract_connections, extract_local_connections


def component(name, bbox):
    return {"id": name, "type": "valve", "tag": name, "bbox": bbox}


def draw_graph(boxes, paths):
    image = Image.new("RGB", (180, 120), "white")
    pen = ImageDraw.Draw(image)
    for item in boxes:
        l, t, r, b = item["bbox"]
        pen.rectangle((l, t, r - 1, b - 1), outline="black", width=2)
    for path in paths:
        pen.line(path, fill="black", width=1)
    return image


def local_graph(boxes, paths):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "test.png"
        draw_graph(boxes, paths).save(path)
        return extract_local_connections(path, boxes)


class LearnedRouteTests(unittest.TestCase):
    def test_centerline_retains_a_straight_pair_beside_a_separate_elbow(self):
        boxes = [component("a", [4, 10, 24, 30]), component("b", [100, 62, 120, 82]),
                 component("c", [5, 90, 25, 110]), component("d", [145, 90, 165, 110])]
        lines = [[(23, 21), (110, 21), (110, 62)], [(24, 100), (145, 100)]]
        result = local_graph(boxes, lines)
        self.assertEqual({(e["source"], e["target"]): e["path"] for e in result["connections"]},
                         {("a", "b"): [[23, 21], [110, 21], [110, 62]],
                          ("c", "d"): [[24, 100], [145, 100]]})

    def test_tee_connects_the_next_symbols_on_each_visible_arm(self):
        boxes = [component("left", [4, 11, 24, 31]), component("right", [150, 11, 170, 31]),
                 component("lower", [70, 85, 90, 110])]
        result = local_graph(boxes, [[(23, 21), (150, 21)], [(80, 21), (80, 85)]])
        self.assertEqual({frozenset((e["source"], e["target"])) for e in result["connections"]},
                         {frozenset(pair) for pair in (("left", "right"), ("left", "lower"), ("right", "lower"))})

    def test_stroke_model_remains_usable_without_numpy(self):
        boxes = [component("a", [4, 10, 24, 30]), component("b", [150, 10, 170, 30])]
        with patch("aip.v3.raster_network.np", None):
            result = local_graph(boxes, [[(23, 21), (150, 21)]])
        self.assertEqual(result["routing_method"], "stroke-model")
        self.assertEqual(result["connections"][0]["path"], [[23, 21], [150, 21]])

    def test_frozen_stroke_model_discriminates_continuity(self):
        for samples in ([True] * 64, [True] * 30 + [False] + [True] * 33):
            self.assertIn(classify_stroke(samples)[0], CONTINUOUS)
        for samples in ([False] * 64, [True] * 25 + [False] * 12 + [True] * 27,
                        ([True] * 4 + [False] * 3) * 10):
            self.assertNotIn(classify_stroke(samples)[0], CONTINUOUS)

    def test_model_stops_at_each_inline_symbol_and_preserves_unread_tag(self):
        boxes = [component("a", [5, 10, 25, 32]), component("b", [60, 10, 80, 32]),
                 component("c", [130, 10, 150, 32])]
        boxes[1]["tag"] = ""
        result = local_graph(boxes, [[(20, 20), (140, 20)]])
        self.assertEqual([(e["source"], e["target"]) for e in result["connections"]], [("a", "b"), ("b", "c")])
        self.assertEqual(result["provider"], "local-ml")
        self.assertEqual(result["training"]["examples"], 960)
        self.assertEqual(result["components"][1]["tag"], "")

    def test_elbow_supported_but_turn_at_crossing_is_withheld(self):
        boxes = [component("a", [4, 10, 24, 30]), component("b", [100, 72, 120, 90])]
        elbow = [(23, 21), (110, 21), (110, 72)]
        result = local_graph(boxes, [elbow])
        self.assertEqual(result["connections"][0]["path"], [[23, 21], [110, 21], [110, 72]])
        crossed = local_graph(boxes, [elbow, [(110, 3), (110, 21)], [(110, 21), (145, 21)]])
        self.assertEqual(crossed["connections"], [])

    def test_vertical_stroke_and_distinct_parallel_lines(self):
        boxes = [component("a", [10, 10, 30, 30]), component("b", [10, 78, 30, 98]),
                 component("c", [110, 10, 130, 30]), component("d", [110, 78, 130, 98])]
        result = local_graph(boxes, [[(20, 29), (20, 78)], [(120, 29), (120, 45)], [(120, 59), (120, 78)]])
        self.assertEqual([(e["source"], e["target"]) for e in result["connections"]], [("a", "b")])
        self.assertEqual(result["connections"][0]["direction"], "unknown")

    def test_flow_arrow_landmarks_survive_a_change_of_extraction_method(self):
        boxes = [component("left", [4, 10, 24, 30]), component("right", [135, 10, 155, 30])]
        paths = [[(23, 20), (135, 20)], [(84, 13), (92, 20), (84, 27)]]
        result = local_graph(boxes, paths)
        edge = result["connections"][0]
        self.assertEqual(edge["direction"], "source_to_target")
        self.assertEqual(result["arrows"][0]["connection_id"], edge["id"])


class VisionRouteTests(unittest.TestCase):
    def run_model(self, add_gap=False, include_middle=False, arrow=None, draw_arrow=False):
        boxes = [component("a", [4, 10, 24, 30]), component("b", [135, 10, 155, 30])]
        if include_middle:
            boxes.append(component("c", [65, 10, 85, 30]))
        path = [(23, 19), (135, 19)]
        paths = [[path[0], (60, 19)], [(100, 19), path[-1]]] if add_gap else [path]
        if draw_arrow:
            paths.append([(56, 15), (65, 19), (56, 24)])
        data = io.BytesIO()
        draw_graph(boxes, paths).save(data, format="PNG")
        edge = {"id": "e", "source": "a", "target": "b", "kind": "pipe", "path": list(reversed(path)),
                "direction": "source_to_target", "evidence": "Single pipe between the two valve outlines."}
        graph = normalize_extraction({"components": boxes, "connections": [edge],
                                     "arrows": [arrow] if arrow else []}, 180, 120)
        graph.update(provider="openai", model="test", image={"sha256": hashlib.sha256(data.getvalue()).hexdigest()})
        with patch("aip.v3.vision.extract_provider", return_value=graph) as provider:
            result = extract_connections({"provider": "openai"}, data.getvalue(), "image/png", 180, 120)
            self.assertEqual(provider.call_count, 1)
        return result

    def test_unattached_or_missing_arrow_cannot_supply_flow_direction(self):
        result = self.run_model(arrow={"id": "ar", "bbox": [55, 80, 75, 98], "tail": [55, 89],
                                      "tip": [65, 89], "connection_id": "e"})
        self.assertEqual(result["connections"][0]["direction"], "unknown")
        self.assertNotIn("connection_id", result["arrows"][0])
        self.assertEqual(self.run_model()["connections"][0]["direction"], "unknown")

    def test_reversed_path_is_aligned_to_endpoints_for_arrow_direction(self):
        result = self.run_model(arrow={"id": "ar", "bbox": [55, 15, 75, 25], "tail": [55, 19],
                                      "tip": [65, 19], "connection_id": "e"}, draw_arrow=True)
        self.assertEqual(result["connections"][0]["direction"], "source_to_target")
        self.assertEqual(result["connections"][0]["path"][0], [23, 19])

    def test_claimed_arrow_over_a_plain_line_leaves_direction_unknown(self):
        result = self.run_model(arrow={"id": "ar", "bbox": [55, 15, 75, 25], "tail": [55, 19],
                                      "tip": [65, 19], "connection_id": "e"})
        self.assertEqual(result["connections"][0]["direction"], "unknown")
        self.assertEqual(result["connections"][0]["direction_evidence"], {
            "basis": "unsupported", "arrow_ids": [], "proposed_direction": "source_to_target"})

    def test_visible_raster_arrow_can_supply_missing_provider_landmark(self):
        result = self.run_model(draw_arrow=True)
        self.assertEqual(result["connections"][0]["direction"], "source_to_target")
        self.assertEqual(result["connections"][0]["direction_evidence"]["basis"], "on_edge")
        arrow = result["arrows"][0]
        self.assertEqual(arrow["origin"], "raster")
        self.assertEqual(arrow["connection_id"], result["connections"][0]["id"])

    def test_shortcut_and_missing_line_remain_outside_graph(self):
        for result in (self.run_model(add_gap=True), self.run_model(include_middle=True)):
            self.assertEqual(result["connections"], [])
            self.assertEqual(len(result["excluded_connections"]), 1)
            self.assertTrue(result["excluded_connections"][0]["exclusion_reason"])

    def test_wrong_image_size_or_type_rejected_before_provider(self):
        data = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(data, format="PNG")
        with patch("aip.v3.vision.extract_provider") as provider:
            for mime, width in (("image/png", 65), ("image/jpeg", 64)):
                with self.assertRaises(ProviderError):
                    extract_connections({"provider": "openai"}, data.getvalue(), mime, width, 64)
            provider.assert_not_called()
