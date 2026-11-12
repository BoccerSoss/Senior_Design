from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from aip.flow_routes import trace_arrow_routes


class FlowRouteTests(unittest.TestCase):
    def scene(self, branch=False, valve_connected=True):
        image = Image.new("L", (360, 180), 0)
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 79, 84, 122), outline=1, width=3)
        draw.line((76, 100, 145, 100), fill=1, width=3)
        draw.line((145, 86, 145, 114), fill=1, width=3)
        draw.line((206, 86, 206, 114), fill=1, width=3)
        if valve_connected:
            draw.line((145, 86, 206, 114), fill=1, width=3)
        draw.line((206, 100, 321, 100), fill=1, width=3)
        draw.polygon(((303, 93), (321, 100), (303, 107)), fill=1)
        if branch:
            draw.line((260, 76, 260, 100), fill=1, width=2)
        components = [
            {"id": "pump", "bbox": [40, 79, 85, 123]},
            {"id": "valve", "bbox": [144, 85, 208, 116]},
        ]
        arrows = [{"id": "arrow", "arrow_tip": [321, 100],
                   "arrow_wings": [[303, 93], [303, 107]], "direction": "right"}]
        return image, arrows, components

    def test_route_follows_connected_ink_through_valve_to_pump(self):
        image, arrows, components = self.scene()
        route, = trace_arrow_routes(image.tobytes(), image.width, arrows, components)
        self.assertEqual(route["arrow_id"], "arrow")
        self.assertEqual(route["component_ids"], ["pump", "valve"])
        self.assertEqual(route["path"][0], [80, 100])
        self.assertEqual(route["path"][-1], arrows[0]["arrow_tip"])
        self.assertTrue(any(y != 100 for _, y in route["path"]))
        # Every drawn route segment overlays the same connected raster pixels.
        for (x0, y0), (x1, y1) in zip(route["path"], route["path"][1:]):
            length = max(abs(x1 - x0), abs(y1 - y0))
            self.assertGreater(length, 0)
            for i in range(length + 1):
                x = x0 + (x1 - x0) * i // length
                y = y0 + (y1 - y0) * i // length
                self.assertEqual(image.getpixel((x, y)), 1)

    def test_route_stops_at_branch_or_disconnected_component(self):
        image, arrows, components = self.scene(branch=True)
        self.assertEqual(trace_arrow_routes(image.tobytes(), image.width, arrows, components), [])
        image, arrows, components = self.scene(valve_connected=False)
        self.assertEqual(trace_arrow_routes(image.tobytes(), image.width, arrows, components), [])
