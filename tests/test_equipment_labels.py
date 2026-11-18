"""Source-pixel label selection, partial OCR words and spatial ambiguities."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from aip.classical import associate_tags
from aip.equipment_ocr import _regions, fill_missing_regions, ink_label_regions, read_damaged_tags, read_ink_labels, recognize_equipment_labels
from aip.windows_ocr import OCRUnavailable


class EquipmentLabels(unittest.TestCase):
    def test_crop_reread_includes_the_prefix_and_cannot_reuse_a_neighbor(self):
        word = {"source_word_bbox":[20, 15, 92, 30]}
        suffix = {"source_word_bbox":[55, 14, 92, 30]}
        alternate = {"source_word_bbox":[158, 14, 222, 30]}
        self.assertEqual(fill_missing_regions([word, None, None], [None, suffix, alternate]),
                         [word, None, alternate])
        prior = {"component_id":"unit", "source_word_bbox":word["source_word_bbox"],
                 "observed_tag":"XY-173"}
        image = Image.new("L", (240, 64), 255)
        with TemporaryDirectory() as temp, patch("aip.equipment_ocr.recognize_label_boxes", return_value=[]) as read:
            read_ink_labels(image, [suffix], [prior], temp)
        self.assertEqual(read.call_args.args[1], [{"id":"unit", "tag_bbox":[20, 14, 92, 30]}])
        self.assertEqual(prior["observed_tag"], "XY-173")

    def test_recovers_printed_tag_when_full_page_ocr_omits_its_words(self):
        for suffix in ("394", "785"):
            with self.subTest(suffix=suffix), TemporaryDirectory() as temp:
                name = "ZX-" + suffix
                image = Image.new("L", (500, 220), 255)
                draw = ImageDraw.Draw(image)
                font = ImageFont.load_default(size=34)
                draw.text((90, 80), name, fill=0, font=font)
                expected = draw.textbbox((90, 80), name, font=font)
                height = expected[3] - expected[1]
                # Full-page OCR only saw another word elsewhere. No transcription
                # or location of the missed label is given to the region finder.
                regions = ink_label_regions(image, [{"text":"Note", "bbox":[10, 10, 55, 10 + height]}])
                self.assertEqual(len(regions), 1)
                component = {"component_id":"pump", "observed_tag":None, "source_word_bbox":None}
                try:
                    read_ink_labels(image, regions, [component], temp)
                except OCRUnavailable as exc:
                    self.skipTest(str(exc))
                self.assertEqual(component["observed_tag"], name)
                self.assertLess(abs(component["source_word_bbox"][0] - expected[0]), 3)

    def test_one_observed_text_region_cannot_name_a_second_piece_of_equipment(self):
        associations = [{"component_id":"a", "observed_tag":"QK-574A", "source_word_bbox":[10, 3, 90, 22]},
                        {"component_id":"b", "observed_tag":None, "source_word_bbox":None}]
        with TemporaryDirectory() as temp:
            readings = read_ink_labels(Image.new("L", (100, 35), 255),
                                       [None, {"source_word_bbox":[7, 2, 92, 24]}], associations, temp)
        self.assertEqual(readings, [])
        self.assertIsNone(associations[1]["observed_tag"])

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
