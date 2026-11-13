"""The glyph matcher follows observed tag pixels without a drawing lookup."""
import unittest

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from aip.glyph_ocr import rank_tag_glyphs


class GlyphOcrTests(unittest.TestCase):
    def test_touching_ink_changes_with_the_glyphs(self):
        font = ImageFont.load_default(size=34)
        for label in ("QK-574A", "QK-574B"):
            with self.subTest(label=label):
                image = Image.new("L", (210, 46), 255)
                ImageDraw.Draw(image).text((5, 0), label, fill=0, font=font)
                image = image.filter(ImageFilter.MinFilter(3))
                readings = rank_tag_glyphs(image)
                self.assertTrue(readings)
                self.assertEqual(readings[0]["text"], label)

    def test_blank_crop_has_no_readings(self):
        self.assertEqual(rank_tag_glyphs(Image.new("L", (210, 46), 255)), [])
