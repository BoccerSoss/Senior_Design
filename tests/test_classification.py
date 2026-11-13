"""Geometry variation and ambiguity tests; not general drawing-accuracy tests."""
import unittest

from PIL import Image, ImageDraw

from aip.classification import classify_symbol


class SimplifiedShapeTests(unittest.TestCase):
    def shape(self, kind, scale=1.0, origin=(50, 45)):
        image = Image.new("RGB", (650, 500), "white")
        draw = ImageDraw.Draw(image)
        ox, oy = origin
        width = round((140 if kind == "valve" else 110) * scale)
        height = round((80 if kind == "valve" else 110) * scale)
        stroke = max(2, round(3*scale))
        x0, y0, x1, y1 = ox, oy, ox+width, oy+height
        cx, cy = (x0+x1)//2, (y0+y1)//2
        if kind == "tank":
            draw.rectangle((x0, y0, x1, y1), outline="black", width=stroke)
        elif kind == "roofed_tank":
            body_top = y0 + round(height * 0.22)
            draw.rectangle((x0, body_top, x1, y1), outline="black", width=stroke)
            draw.line([(x0, body_top), (cx, y0), (x1, body_top)], fill="black", width=stroke)
        elif kind == "valve":
            draw.line([(x0, y0), (cx, cy), (x0, y1), (x0, y0)], fill="black", width=stroke)
            draw.line([(x1, y0), (cx, cy), (x1, y1), (x1, y0)], fill="black", width=stroke)
        elif kind in {"pump", "circle"}:
            draw.ellipse((x0, y0, x1, y1), outline="black", width=stroke)
            if kind == "pump":
                draw.line((x0, cy, cx, cy), fill="black", width=stroke)
                draw.line((cx, y0, x1, y0), fill="black", width=stroke)
        elif kind == "diamond":
            draw.line([(cx, y0), (x1, cy), (cx, y1), (x0, cy), (cx, y0)], fill="black", width=stroke)
        elif kind == "cross":
            draw.line((cx, y0, cx, y1), fill="black", width=stroke)
            draw.line((x0, cy, x1, cy), fill="black", width=stroke)
        elif kind == "filled":
            draw.rectangle((x0, y0, x1, y1), fill="black")
        return image, [x0-3, y0-3, x1+4, y1+4]

    def test_supported_shapes_across_scale_and_position(self):
        for kind, expected in (("tank", "tank"), ("roofed_tank", "tank"), ("valve", "valve"), ("pump", "pump")):
            for scale, origin in ((0.6, (30, 40)), (1.0, (140, 95)), (1.8, (330, 210))):
                with self.subTest(kind=kind, scale=scale):
                    image, box = self.shape(kind, scale, origin)
                    result = classify_symbol(image, box)
                    self.assertEqual(result["type"], expected, result)
                    self.assertTrue(result["evidence"])
                    self.assertTrue(result["uncertainty"])

    def test_ambiguous_or_unsupported_shapes_stay_unknown(self):
        for kind in ("circle", "diamond", "cross", "filled"):
            with self.subTest(kind=kind):
                image, box = self.shape(kind)
                self.assertEqual(classify_symbol(image, box)["type"], "unknown")

    def test_text_outside_box_cannot_supply_a_type(self):
        image, box = self.shape("circle")
        before = classify_symbol(image, box)
        ImageDraw.Draw(image).text((300, 20), "TANK PUMP VALVE ANY-TAG", fill="black")
        self.assertEqual(classify_symbol(image, box), before)

    def test_incomplete_pump_strokes_do_not_resolve_plain_circle(self):
        for stroke in ("center_entry", "tangent_outlet"):
            with self.subTest(stroke=stroke):
                image, box = self.shape("circle")
                draw = ImageDraw.Draw(image)
                if stroke == "center_entry":
                    draw.line((50, 100, 105, 100), fill="black", width=3)
                else:
                    draw.line((105, 45, 160, 45), fill="black", width=3)
                self.assertEqual(classify_symbol(image, box)["type"], "unknown")

    def test_blank_and_tiny_candidates_stay_unknown(self):
        image = Image.new("RGB", (100, 100), "white")
        self.assertEqual(classify_symbol(image, [10, 10, 70, 70])["type"], "unknown")
        self.assertEqual(classify_symbol(image, [10, 10, 20, 20])["type"], "unknown")

    def test_invalid_boxes_rejected(self):
        image = Image.new("RGB", (100, 100), "white")
        for box in ([0, 0, 101, 90], [10, 10, 10, 20], [0, 0, float("nan"), 20], [False, 0, 50, 50]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                classify_symbol(image, box)


if __name__ == "__main__":
    unittest.main()
