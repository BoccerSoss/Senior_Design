from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from aip.desktop_analysis import analyze_drawing, prepare_label_review
from aip.drawing_reference import REFERENCE_DIR, matching_reference
from aip.io import load_json, prepare_input, write_json


class DrawingReferenceTests(unittest.TestCase):
    def test_bundled_geometry_contains_no_equipment_names(self):
        records = [load_json(path) for path in REFERENCE_DIR.glob("nureg-cr5745-*.json")]
        self.assertEqual(len(records), 6)
        for record in records:
            for item in record["observations"]["components"]:
                self.assertRegex(item["id"], r"^symbol-\d+$")
                self.assertNotIn("observed_tag", item)
                self.assertNotIn("tag", item)

    def test_matching_reference_runs_ocr_and_exports_separate_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "drawing.png"
            image = Image.new("RGB", (200, 100), "white")
            ImageDraw.Draw(image).text((30, 45), "ZX-58", fill="black")
            image.save(image_path)
            info = prepare_input(image_path, root / "packet")["drawing"]
            library = root / "reference"
            library.mkdir()
            path = library / "example.json"
            label_box = [28, 40, 85, 67]
            write_json(path, {
                "citation": "Geometry test fixture", "source_url": "https://example.com",
                "image": {key: info[key] for key in ("sha256", "width", "height")},
                "observations": {"image": {"width": 200, "height": 100},
                                 "components": [{"id": "symbol-01", "type": "valve", "bbox": [24, 18, 56, 34],
                                                 "tag_bbox": label_box, "evidence": None, "uncertainty": None}],
                                 "connections": []},
            })
            match = matching_reference(info, library)
            self.assertIsNotNone(match)
            modified = {**info, "sha256": "0" * 64, "source_sha256": "0" * 64}
            self.assertIsNone(matching_reference(modified, library))
            with patch("aip.desktop_analysis.matching_reference", return_value=match), \
                 patch("aip.desktop_analysis.process_run") as automatic, \
                 patch("aip.drawing_reference.recognize_label_boxes",
                       return_value=[{"component_id": "symbol-01", "observed_tag": "ZX-58"}]) as ocr:
                result = analyze_drawing(image_path, root / "runs")
            automatic.assert_not_called()
            self.assertEqual(ocr.call_args.args[1], [{"id": "symbol-01", "tag_bbox": label_box}])
            self.assertEqual(result.equipment, [("ZX-58", "valve")])
            self.assertEqual(result.markings["reference"]["citation"], "Geometry test fixture")
            self.assertEqual(load_json(result.directory / "run.json")["method"], "drawing-reference")
            self.assertTrue((result.directory / "ocr_evidence.json").is_file())
            upload = prepare_label_review(result)
            self.assertEqual({item.name for item in upload.iterdir()}, {"label-crops.png", "prompt.txt"})
            self.assertNotIn("ZX-58", (upload / "prompt.txt").read_text(encoding="utf-8"))
            response = root / "response.json"
            write_json(response, {"drawing_key": info["sha256"][:16],
                                  "labels": [{"id": "symbol-01", "text": "ZX-59"}]})
            with patch("aip.desktop_analysis.matching_reference", return_value=match), \
                 patch("aip.drawing_reference.recognize_label_boxes") as ocr:
                imported = analyze_drawing(image_path, root / "runs", label_response=response)
            ocr.assert_not_called()
            self.assertTrue(imported.vision_transcription)
            self.assertEqual(imported.equipment, [("ZX-59", "valve")])
            self.assertEqual((imported.directory / "vision_response.txt").read_bytes(), response.read_bytes())
            grid = next(mark for mark in imported.markings["marks"] if mark["kind"] == "equipment_grid_label")
            self.assertEqual(grid["text"], "(1,1)")
            self.assertGreaterEqual(grid["bbox"][1], label_box[3])
