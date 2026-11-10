"""Optional Windows OCR checks; test labels are drawn pixels, never truth fixes."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from aip.windows_ocr import OCRUnavailable, _original_box, recognize_image, recognize_tag_crops


class WindowsOCRAvailabilityTests(unittest.TestCase):
    def test_documented_clockwise_box_rotation(self):
        box, polygon = _original_box([70,40,90,60],90,100,100)
        self.assertEqual(box,[40,70,60,90])
        self.assertEqual(len(polygon),4)

    def test_nonwindows_has_explicit_optional_engine_diagnostic(self):
        with patch("aip.windows_ocr.sys.platform", "linux"):
            with self.assertRaisesRegex(OCRUnavailable, "requires Windows"):
                recognize_image("unused.png")

    @unittest.skipUnless(sys.platform == "win32", "Windows optional engine")
    def test_missing_image_is_not_replaced_with_fixture(self):
        with self.assertRaisesRegex(OCRUnavailable, "does not exist"):
            recognize_image("image-that-does-not-exist.png")

    @unittest.skipUnless(sys.platform == "win32", "Windows optional engine")
    def test_invalid_image_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            invalid = Path(temp) / "invalid.png"
            invalid.write_bytes(b"not an image")
            with self.assertRaisesRegex(OCRUnavailable, "unavailable"):
                recognize_image(invalid)


@unittest.skipUnless(sys.platform == "win32", "Windows optional engine")
class WindowsOCRPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.blank = cls.root / "blank.png"
        Image.new("RGB", (1100, 600), "white").save(cls.blank)
        try:
            cls.blank_result = recognize_image(cls.blank)
        except OCRUnavailable as exc:
            cls.temporary.cleanup()
            raise unittest.SkipTest(f"Optional installed Windows OCR recognizer unavailable: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def font(self):
        # Windows font chosen for independently drawn control labels; no source
        # SVG, answer annotations, generator coordinates or expected graph read.
        return ImageFont.truetype("arial.ttf", 46)

    def test_blank_returns_no_memorized_words(self):
        self.assertEqual(self.blank_result["words"], [])
        self.assertEqual(self.blank_result["lines"], [])

    def test_changed_labels_and_locations_change_observations(self):
        observations = []
        for index, (text, location) in enumerate((("EQ-742", (90, 80)), ("ZX-836", (610, 390)))):
            image = Image.new("RGB", (1100, 600), "white")
            ImageDraw.Draw(image).text(location, text, fill="black", font=self.font())
            filename = self.root / f"independent-label-{index}.png"
            image.save(filename)
            result = recognize_image(filename)
            self.assertEqual(result["image"], {"width": 1100, "height": 600})
            self.assertIn(result["language"], result["available_languages"])
            matching = [w for w in result["words"] if w["text"].upper() == text]
            self.assertTrue(matching, result["words"])
            word = matching[0]
            self.assertIsNone(word["confidence"])
            self.assertIsNotNone(word["bbox"])
            self.assertLess(abs(word["bbox"][0] - location[0]), 20)
            self.assertLess(abs(word["bbox"][1] - location[1]), 30)
            observations.append(word)
        self.assertNotEqual(observations[0]["text"], observations[1]["text"])
        self.assertGreater(observations[1]["bbox"][0] - observations[0]["bbox"][0], 400)
        self.assertGreater(observations[1]["bbox"][1] - observations[0]["bbox"][1], 200)

    def test_optional_clean_raster_recognition_is_image_observation(self):
        # Only input raster is opened; no reference/source is used or asserted.
        image = Path(__file__).resolve().parents[1] / "data/input/four_component.png"
        if not image.is_file():
            self.skipTest("Synthetic clean raster not present in this checkout")
        result = recognize_image(image)
        self.assertEqual(result["image"], {"width": 1800, "height": 800})
        self.assertTrue(result["words"])
        self.assertTrue(any(w["bbox"] is not None for w in result["words"]))
        self.assertTrue(all(w["confidence"] is None for w in result["words"]))

    def test_suffix_passes_retain_raw_and_follow_independent_control_pixels(self):
        image = Image.new("RGB",(1100,600),"white")
        ImageDraw.Draw(image).text((610,390),"ZX-836",font=self.font(),fill="black")
        filename = self.root / "suffix-independent-control.png"
        image.save(filename)
        scratch = self.root / "retained-suffix-evidence"
        with patch("aip.windows_ocr.recognize_image", wraps=recognize_image) as calls:
            result = recognize_tag_crops(filename,scratch_dir=scratch)
        self.assertEqual(calls.call_count,3)
        self.assertTrue(all(Path(call.kwargs["working_directory"]).resolve()==scratch.resolve() for call in calls.call_args_list))
        records = [p for p in result["tag_crop_passes"] if p["original_text"].upper() == "ZX-836"]
        self.assertEqual(len(records),1,result)
        self.assertEqual(records[0]["selected_text"].upper(),"ZX-836")
        self.assertEqual([p["scale"] for p in records[0]["passes"]],[1,3])
        self.assertTrue(any(w["text"].upper()=="ZX-836" for w in result["native"]["words"]))
        self.assertTrue(result["crop_artifacts_retained"])
        self.assertTrue((scratch/"native.ocr.json").is_file())
        for item in records[0]["passes"]:
            self.assertTrue((scratch/item["crop_image_filename"]).is_file())
            self.assertEqual(json.loads((scratch/item["ocr_json_filename"]).read_text())["words"],item["ocr"]["words"])


if __name__ == "__main__":
    unittest.main()
