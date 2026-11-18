"""Behavior controls for a geometry baseline; these do not measure P&ID accuracy."""
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from aip.baseline import _candidate_boxes, _ink_components, extract_image


class BaselineControls(unittest.TestCase):
    def test_same_columns_on_separate_rows_yield_individual_symbol_boxes(self):
        image = Image.new("L", (720, 490))
        draw = ImageDraw.Draw(image)
        for y in (110, 295):
            draw.line((80, y, 597, y), fill=1, width=3)
            for x in (216, 418):
                draw.polygon([(x-24, y-16), (x+24, y+16), (x+24, y-16), (x-24, y+16)],
                             outline=1, width=3)
        draw.line((80, 110, 80, 295), fill=1, width=3)
        network, = _ink_components(image.tobytes(), *image.size)
        boxes = _candidate_boxes(network[0], image.width)
        self.assertEqual(len(boxes), 4)
        for x in (216, 418):
            for y in (110, 295):
                expected = (x-24, y-16, x+25, y+17)
                self.assertTrue(any(max(abs(a-b) for a, b in zip(box, expected)) <= 2 for box in boxes))

    def test_large_standalone_rectangle_survives_pipe_stroke_removal(self):
        image = Image.new("L", (700, 450))
        ImageDraw.Draw(image).rectangle((50, 80, 340, 200), outline=1, width=3)
        network, = _ink_components(image.tobytes(), *image.size)
        self.assertEqual([[50, 80, 341, 201]], _candidate_boxes(network[0], image.width))

    def test_connected_ink_spans_follow_diagonals_and_stop_at_row_boundaries(self):
        image = Image.new("L", (120, 120))
        draw = ImageDraw.Draw(image)
        draw.line((20, 10, 59, 49), fill=255)
        draw.line((100, 70, 119, 70), fill=255)
        draw.line((0, 71, 0, 110), fill=255)
        groups = list(_ink_components(image.tobytes(), *image.size))
        self.assertEqual(len(groups), 1)
        points, box = groups[0]
        self.assertEqual(box, (20, 10, 60, 50))
        self.assertEqual(set(points), {(20 + i, 10 + i) for i in range(40)})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def diagram(self, connected=True, arrows=(), crossing=False):
        # Generic test shapes and coordinates differ from the drawing fixture.
        image = Image.new("RGB", (700, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 90, 170, 210), outline="black", width=4)
        draw.ellipse((450, 100, 550, 200), outline="black", width=4)
        if connected:
            draw.line((170, 150, 450, 150), fill="black", width=4)
        else:
            draw.line((170, 150, 290, 150), fill="black", width=4)
            draw.line((320, 150, 450, 150), fill="black", width=4)
        for tip_x, signed_length, wing_height, filled in arrows:
            rear_x = tip_x - signed_length
            arrow = [(rear_x, 150 - wing_height), (tip_x, 150), (rear_x, 150 + wing_height)]
            if filled:
                draw.polygon(arrow, fill="black")
            else:
                draw.line(arrow, fill="black", width=3)
        if crossing:
            draw.line((280, 140, 315, 160), fill="black", width=3)
        path = self.root / ("connected.png" if connected else "disconnected.png")
        image.save(path)
        return path

    def test_blank_has_no_memorized_graph(self):
        path = self.root / "blank.png"
        Image.new("RGB", (700, 300), "white").save(path)
        result = extract_image(path)
        self.assertEqual(result["components"], [])
        self.assertEqual(result["connections"], [])
        self.assertTrue(result["uncertainties"])

    def test_removing_visible_pipe_changes_connections(self):
        connected = extract_image(self.diagram(True))
        disconnected = extract_image(self.diagram(False))
        self.assertEqual(len(connected["connections"]), 1)
        self.assertEqual(len(disconnected["connections"]), 0)
        self.assertEqual(len(connected["components"]), len(disconnected["components"]))
        self.assertTrue(all(c["observed_tag"] is None for c in connected["components"]))

    def test_jpeg_pixels_are_consumed(self):
        path = self.root / "test.jpg"
        Image.open(self.diagram()).save(path, quality=95)
        result = extract_image(path)
        self.assertEqual(result["image"], {"width": 700, "height": 300})
        self.assertGreater(len(result["components"]), 0)

    def test_connection_path_is_on_the_visible_stroke(self):
        connection, = extract_image(self.diagram())["connections"]
        self.assertEqual(connection["path"], [[170, 150], [450, 150]])
        self.assertEqual(connection["direction"], {"status": "unknown", "arrow_tip": None, "arrow_wings": None})

    def test_direction_follows_an_on_pipe_arrow(self):
        cases = [(300, 20, 20, False, "source_to_target"),
                 (330, -20, 20, True, "target_to_source"),
                 (305, 40, 10, True, "source_to_target")]
        for tip, length, height, filled, expected in cases:
            with self.subTest(expected=expected):
                result = extract_image(self.diagram(arrows=[(tip, length, height, filled)]))
                # A visible arrow is flow evidence for the same pipe, not a third equipment item.
                self.assertEqual(len(result["components"]), 2)
                connection, = result["connections"]
                self.assertEqual(connection["path"], [[170, 150], [450, 150]])
                direction = connection["direction"]
                self.assertEqual(direction["status"], expected)
                tip_x, tip_y = direction["arrow_tip"]
                self.assertAlmostEqual(tip_x, tip, delta=4)
                self.assertEqual(tip_y, 150)
                upper, lower = direction["arrow_wings"]
                self.assertLess(upper[1], tip_y)
                self.assertGreater(lower[1], tip_y)
                self.assertTrue(all((tip_x - x) * length > 0 for x, _ in (upper, lower)))

    def test_conflicting_arrows_and_crossings_keep_direction_unknown(self):
        for drawing in ({"arrows": [(280, 20, 10, False), (340, -20, 10, False)]}, {"crossing": True}):
            with self.subTest(drawing=drawing):
                connection, = extract_image(self.diagram(**drawing))["connections"]
                self.assertEqual(connection["direction"], {"status": "unknown", "arrow_tip": None, "arrow_wings": None})

    def test_not_reading_reference_or_neighbor_files(self):
        # The same image produces the same output despite poisoned neighbor data.
        path = self.diagram()
        before = extract_image(path)
        (self.root / "reference.json").write_text(json.dumps({"components": ["POISON-TAG"], "edges": []}))
        after = extract_image(path)
        self.assertEqual(before, after)
        self.assertNotIn("POISON-TAG", json.dumps(after))

    def test_unsupported_format_is_explicit(self):
        path = self.root / "test.gif"
        Image.new("RGB", (50, 50), "white").save(path)
        with self.assertRaisesRegex(ValueError, "PNG/JPEG"):
            extract_image(path)


if __name__ == "__main__":
    unittest.main()
