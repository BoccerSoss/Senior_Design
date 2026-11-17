"""Source-pixel label selection, partial OCR words and spatial ambiguities."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from aip.classical import associate_tags
from aip.equipment_ocr import _regions, read_damaged_tags, recognize_equipment_labels
from aip.windows_ocr import OCRUnavailable


class EquipmentLabels(unittest.TestCase):
    def test_words_from_distinct_ocr_lines_join_only_when_adjacent(self):
        words = [{"text": text, "bbox": box} for text, box in [
            ("West", [10, 10, 40, 25]), ("Pump", [50, 11, 89, 26]), ("B", [98, 10, 109, 25]),
            ("ZX-", [165, 10, 196, 25]), ("394", [200, 10, 222, 25]),
            ("Figure", [40, 74, 80, 90]), ("Cold", [222, 75, 244, 90])]]
        reading = {"words": list(reversed(words))}
        self.assertEqual(list(_regions(reading)), [
            ("West Pump B", [10, 10, 109, 26]), ("ZX- 394", [165, 10, 222, 25])])

    def test_below_and_side_labels_skip_junction_and_crossing(self):
        components = [{"id": name, "bbox": box, "type": kind} for name, box, kind in [
            ("first", [30, 30, 80, 60], "pump"), ("second", [220, 30, 260, 60], "valve"),
            ("fork", [400, 30, 420, 50], "junction")]]
        records = [{"selected_text": text, "original_text": text, "source_word_bbox": box, "selection": "pixel reading"}
                   for text, box in [("West Pump B", [20, 70, 90, 85]), ("ZX-394", [280, 37, 335, 52]),
                                     ("QZ-18", [383, 60, 431, 75])]]
        self.assertEqual([r["observed_tag"] for r in associate_tags(components, records, 250)],
                         ["West Pump B", "ZX-394", None])
        containing = {"id": "merged", "bbox": [10, 40, 180, 150], "type": "unknown"}
        self.assertIsNone(associate_tags([containing], records, 250)[0]["observed_tag"])

    def test_damaged_tag_needs_a_clear_winner_and_work_is_bounded(self):
        records = [{"original_text": "9--6X4", "read_bounds": [0, 0, 10, 10], "selected_text": None}
                   for _ in range(12)]
        results = [{"text": "ZK-684", "mismatch": 0.202}, {"text": "ZK-604", "mismatch": 0.203}]
        with patch("aip.equipment_ocr.rank_tag_glyphs", return_value=results) as rank:
            read_damaged_tags(Image.new("L", (10, 10), 255), records)
        self.assertEqual(8, rank.call_count)
        self.assertTrue(all(r["selected_text"] is None for r in records))

    def test_visible_name_changes_with_same_symbol_location(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for index, name in enumerate(("East Pump B", "West Pump C")):
                image = Image.new("L", (650, 250), 255)
                ImageDraw.Draw(image).text((50, 62), name, fill=0, font=ImageFont.load_default(size=34))
                page = root / f"{index}.png"
                image.save(page)
                try:
                    reading = recognize_equipment_labels(page, root / f"scratch-{index}")
                except OCRUnavailable as exc:
                    self.skipTest(str(exc))
                self.assertEqual([name], [r["selected_text"] for r in reading["tag_crop_passes"]])
