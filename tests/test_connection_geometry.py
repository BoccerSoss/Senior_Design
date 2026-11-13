"""Pixel controls for arrows that fall outside a paired equipment span."""
import unittest

from PIL import Image, ImageDraw

from aip.connection_geometry import observe_flow_arrows


class VisibleArrowTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("L", (700, 400), 0)
        self.draw = ImageDraw.Draw(self.image)

    def observations(self):
        return observe_flow_arrows(self.image.tobytes(), self.image.width)

    def assert_near(self, point, expected, tolerance=5):
        self.assertLessEqual(max(abs(a - b) for a, b in zip(point, expected)), tolerance)

    def test_filled_heads_at_a_junction_and_vertical_terminal(self):
        self.draw.line((80, 110, 300, 110), fill=1, width=4)
        self.draw.line((300, 65, 300, 160), fill=1, width=5)
        self.draw.polygon([(258, 100), (300, 110), (258, 120)], fill=1)
        self.draw.line((470, 75, 470, 220), fill=1, width=4)
        self.draw.polygon([(460, 115), (470, 75), (480, 115)], fill=1)
        up, right = self.observations()
        self.assertEqual((up["direction"], right["direction"]), ("up", "right"))
        self.assert_near(up["arrow_tip"], [470, 75])
        self.assert_near(right["arrow_tip"], [300, 110])
        self.assertTrue(all(y > up["arrow_tip"][1] for _, y in up["arrow_wings"]))
        self.assertTrue(all(x < right["arrow_tip"][0] for x, _ in right["arrow_wings"]))

    def test_shallow_diagonal_uses_original_image_coordinates(self):
        self.draw.line((85, 220, 235, 270), fill=1, width=4)
        self.draw.line((85, 195, 85, 245), fill=1, width=4)
        self.draw.polygon([(85, 220), (125, 222), (125, 244)], fill=1)
        arrow, = self.observations()
        self.assertEqual(arrow["direction"], "up-left")
        self.assert_near(arrow["arrow_tip"], [85, 220])
        self.assertTrue(all(x > arrow["arrow_tip"][0] for x, _ in arrow["arrow_wings"]))

    def test_shallow_diagonal_at_wide_scan_tile_boundary(self):
        image = Image.new("L", (2500, 150), 0)
        draw = ImageDraw.Draw(image)
        draw.line((1035, 60, 1185, 110), fill=1, width=4)
        draw.line((1035, 35, 1035, 85), fill=1, width=4)
        draw.polygon([(1035, 60), (1075, 62), (1075, 84)], fill=1)
        arrow, = observe_flow_arrows(image.tobytes(), image.width)
        self.assertEqual(arrow["direction"], "up-left")
        self.assert_near(arrow["arrow_tip"], [1035, 60])

    def test_crossing_and_opposed_dimension_heads_are_excluded(self):
        self.draw.line((65, 100, 400, 100), fill=1, width=4)
        self.draw.polygon([(65, 100), (105, 90), (105, 110)], fill=1)
        self.draw.polygon([(400, 100), (360, 90), (360, 110)], fill=1)
        self.draw.line((200, 75, 200, 150), fill=1, width=4)
        self.draw.line((100, 245, 360, 245), fill=1, width=4)
        self.draw.line((275, 215, 275, 275), fill=1, width=4)
        self.assertEqual(self.observations(), [])
