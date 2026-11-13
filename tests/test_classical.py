"""Association/adapter plumbing fixtures, not scientific OCR predictions."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from aip.classical import associate_tags, extract_image
from aip.windows_ocr import OCRUnavailable


def candidate(ident, box):
    return {"id": ident, "observed_tag": None, "type": "unknown", "bbox": box,
            "evidence": "unit fixture geometry, not an extraction", "uncertainty": None}


def text_record(text, box):
    return {"selected_text": text, "source_word_bbox": box, "original_text": text,
            "selection": "unit fixture OCR response; software plumbing only"}


class TagAssociationTests(unittest.TestCase):
    def test_variable_component_counts_and_arbitrary_prefixes(self):
        for count in (0,1,3,5):
            with self.subTest(count=count):
                components = [candidate(f"o{i}",[30+150*i,160,130+150*i,260]) for i in range(count)]
                records = [text_record(f"ZX-{742+i}",[50+150*i,110,110+150*i,140]) for i in range(count)]
                result = associate_tags(components,records,400)
                self.assertEqual([r["observed_tag"] for r in result],[r["selected_text"] for r in records])
                self.assertEqual(len(result),count)

    def test_missing_and_below_symbol_tags_remain_unresolved(self):
        components = [candidate("unresolved",[30,160,130,260])]
        for records in ([],[text_record("QX-836",[50,210,110,240])]):
            result = associate_tags(components,records,400)
            self.assertIsNone(result[0]["observed_tag"])
            self.assertIn("unresolved",result[0]["uncertainty"])

    def test_competing_text_boxes_remain_unresolved(self):
        components = [candidate("ambiguous",[30,160,130,260])]
        records = [text_record("ZX-742",[50,110,110,140]),text_record("QX-836",[51,110,111,140])]
        result = associate_tags(components,records,400)
        self.assertIsNone(result[0]["observed_tag"])

    def test_one_text_competing_equally_for_two_symbols_is_order_independent(self):
        components = [candidate("left",[20,80,120,160]),candidate("right",[80,80,180,160])]
        records = [text_record("ZZ-742",[90,40,110,60])]
        first = associate_tags(components,records,300)
        reverse = associate_tags(list(reversed(components)),records,300)
        self.assertTrue(all(a["observed_tag"] is None for a in first))
        self.assertEqual({a["component_id"]:a["observed_tag"] for a in first},
                         {a["component_id"]:a["observed_tag"] for a in reverse})

    def test_text_is_used_once_when_one_symbol_is_clearly_closer(self):
        components = [candidate("near",[20,80,120,160]),candidate("far",[80,80,180,160])]
        records = [text_record("ZZ-742",[60,40,80,60])]
        result = associate_tags(components,records,300)
        self.assertEqual([a["observed_tag"] for a in result],["ZZ-742",None])


class ClassicalCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "fixture.png"
        image = Image.new("RGB",(320,280),"white")
        ImageDraw.Draw(image).rectangle((50,120,160,240),outline="black",width=4)
        image.save(self.path)
        self.raw = {"image":{"width":320,"height":280},"components":[candidate("region",[50,120,161,241])],
                    "connections":[],"uncertainties":[]}

    def tearDown(self):
        self.temporary.cleanup()

    def ocr_fixture(self,text):
        return {"native":{"image":{"width":320,"height":280},"words":[]},
                "tag_crop_passes":[text_record(text,[65,70,145,100])],"crop_artifacts_retained":False}

    def test_type_comes_from_pixels_even_when_tag_prefix_suggests_another_type(self):
        # Mocked OCR/geometry test orchestration only. The actual classifier sees
        # the drawn rectangular outline, not the selected label or a reference.
        with patch("aip.classical.geometry_extract",return_value=copy.deepcopy(self.raw)), \
             patch("aip.classical.recognize_tag_crops",return_value=self.ocr_fixture("P-825")):
            result = extract_image(self.path,self.root/"scratch")
        self.assertEqual(result["components"][0]["observed_tag"],"P-825")
        self.assertEqual(result["components"][0]["type"],"tank")
        self.assertIn("Raster",result["components"][0]["evidence"])

    def test_duplicate_ocr_tags_are_unresolved_and_retain_their_evidence(self):
        raw = {**self.raw, "components": [
            candidate("left", [20, 120, 90, 240]),
            candidate("middle", [105, 120, 175, 240]),
            candidate("right", [200, 120, 275, 240]),
        ]}
        tags = ["ZZ-742", "zz-742", "QX-836"]
        boxes = [[25, 70, 85, 100], [110, 70, 170, 100], [205, 70, 265, 100]]
        fixture = self.ocr_fixture(tags[0])
        fixture["tag_crop_passes"] = [text_record(tag, box) for tag, box in zip(tags, boxes)]
        with patch("aip.classical.geometry_extract", return_value=raw), \
             patch("aip.classical.recognize_tag_crops", return_value=fixture):
            result = extract_image(self.path, self.root / "scratch")
        components = result["components"]
        self.assertEqual([item["observed_tag"] for item in components], [None, None, "QX-836"])
        self.assertEqual([item["tag_bbox"] for item in components], boxes)
        self.assertEqual([item["observed_tag"] for item in result["method_evidence"]["associations"]], tags)
        for item, tag in zip(components[:2], tags):
            self.assertIn(tag, item["evidence"])
            self.assertIn("Identity is unresolved", item["uncertainty"])

    def test_unavailable_ocr_fails_without_inventing_labels(self):
        raw = copy.deepcopy(self.raw)
        with patch("aip.classical.geometry_extract",return_value=raw), \
             patch("aip.classical.recognize_tag_crops",side_effect=OCRUnavailable("unit fixture: OCR unavailable")):
            with self.assertRaisesRegex(OCRUnavailable,"unavailable"):
                extract_image(self.path,self.root/"scratch")
        self.assertIsNone(raw["components"][0]["observed_tag"])

    def test_empty_image_does_not_invoke_or_replace_unavailable_ocr(self):
        raw = {"image":{"width":320,"height":280},"components":[],"connections":[],"uncertainties":[]}
        with patch("aip.classical.geometry_extract",return_value=raw), \
             patch("aip.classical.recognize_tag_crops",side_effect=AssertionError("OCR should not be called")):
            result = extract_image(self.path,self.root/"scratch")
        self.assertEqual(result["components"],[])
        self.assertEqual(result["connections"],[])

    def test_ocr_image_dimension_mismatch_is_rejected(self):
        fixture = self.ocr_fixture("ZZ-742")
        fixture["native"]["image"]["width"] = 999
        with patch("aip.classical.geometry_extract",return_value=copy.deepcopy(self.raw)), \
             patch("aip.classical.recognize_tag_crops",return_value=fixture):
            with self.assertRaisesRegex(ValueError,"dimensions disagree"):
                extract_image(self.path,self.root/"scratch")


if __name__ == "__main__":
    unittest.main()
