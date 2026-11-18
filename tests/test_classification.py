"""Geometry variation and ambiguity tests; not general drawing-accuracy tests."""
import unittest

from PIL import Image, ImageDraw

from aip.classification import classify_symbol, refine_symbol_boxes


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

    def test_valve_strokes_with_ports_operators_and_rotation(self):
        for kind in ("slanted", "filled", "outline"):
            image = Image.new("L", (156, 96), "white")
            draw = ImageDraw.Draw(image)
            draw.line((0,60,32,60), fill=0, width=3)
            draw.line((116,60,155,60), fill=0, width=3)
            if kind == "slanted":
                draw.line([(32,83),(32,37),(116,83),(116,37)], fill=0, width=4)
            else:
                for x in (32,116):
                    points = [(x,38),(74,60),(x,82),(x,38)]
                    if kind == "filled":
                        draw.polygon(points, fill=0)
                    else:
                        draw.line(points, fill=0, width=3)
                draw.line((74,60,74,25), fill=0, width=3)
                draw.rectangle((61,4,87,25), outline=0, width=3)
            for view in (image, image.transpose(Image.Transpose.ROTATE_90)):
                with self.subTest(kind=kind, size=view.size):
                    self.assertEqual(classify_symbol(view, [0,0,*view.size])["type"], "valve")

    def test_scroll_pump_and_rounded_vessel(self):
        pump = Image.new("L", (154, 103), "white")
        d = ImageDraw.Draw(pump)
        d.arc((28,2,117,100), 0,270, fill=0, width=4)
        d.line([(72,3),(139,3),(139,51),(115,51)], fill=0, width=4)
        d.line((0,50,29,50), fill=0, width=3)
        self.assertEqual(classify_symbol(pump, [0,0,*pump.size])["type"], "pump")
        vessel = Image.new("L", (88, 212), "white")
        ImageDraw.Draw(vessel).rounded_rectangle((4,2,84,210), radius=40, outline=0, width=4)
        self.assertEqual(classify_symbol(vessel, [0,0,*vessel.size])["type"], "vessel")

    def test_split_adjacent_valves_and_prune_only_whole_arrowheads_and_corners(self):
        image = Image.new("L",(425,170),"white")
        d = ImageDraw.Draw(image)
        for x in (22,214):
            d.line([(x,92),(x,40),(x+80,92),(x+80,40)], fill=0, width=4)
        d.line((102,65,214,65), fill=0, width=3)
        d.line((302,116,388,116), fill=0, width=2)
        d.polygon([(325,106),(345,116),(325,126)], fill=0)
        d.line([(320,40),(360,40),(360,80)], fill=0,width=3)
        marks = [{"arrow_tip":[345,116],"arrow_wings":[[325,106],[325,126]]}]
        boxes = [[18,36,300,96],[325,106,346,127],[319,39,363,82]]
        repaired = refine_symbol_boxes(image, boxes, marks)
        self.assertEqual(len(repaired),2)
        self.assertLess(repaired[0][2],repaired[1][0])
        self.assertEqual([classify_symbol(image,b)["type"] for b in repaired], ["valve","valve"])


if __name__ == "__main__":
    unittest.main()
