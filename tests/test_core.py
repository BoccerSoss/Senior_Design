from __future__ import annotations

import copy
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from aip.core import ValidationError, normalize_response, normalize_tag, validate_document
from aip.evaluation import evaluate
from aip.exporting import export_document


DRAWING = {"drawing_id": "unit-fixture", "filename": "unit.png", "sha256": "a" * 64, "width": 200, "height": 100}
RUN = {"run_id": "unit-evaluator-only", "method": "known fixture, not extraction",
       "timestamp": "2026-11-10T12:00:00-05:00", "timezone": "America/New_York",
       "provider": "unit fixture", "model": None, "access_route": "evaluator fixture only",
       "settings": {}, "raw_response_location": "test_core.py", "manual_stages": [],
       "duration_seconds": None, "code": {"revision": None, "dirty_state": None},
       "prompt_version": "unit-fixture", "prompt_sha256": "b" * 64}


def raw(tags=("A-7", "B-8", "C-9"), edges=((0, 1), (1, 2))):
    return {"image": {"width": 200, "height": 100},
            "components": [{"id": f"o{i}", "observed_tag": tag, "type": "tank", "bbox": None,
                            "evidence": None, "uncertainty": None} for i, tag in enumerate(tags)],
            "connections": [{"source": f"o{a}", "target": f"o{b}", "kind": "direct_undirected",
                             "evidence": None, "uncertainty": None} for a, b in edges], "uncertainties": []}


def reference():
    return {"schema_version": "1.0.0", "drawing_id": "unit-fixture", "image": {"sha256": "a"*64, "width": 200, "height": 100},
            "components": [{"id": tag, "tag": tag, "type": "tank"} for tag in ("A-7", "B-8", "C-9")],
            "edges": [{"source": "A-7", "target": "B-8"}, {"source": "B-8", "target": "C-9"}],
            "matrix_order": ["A-7", "B-8", "C-9"], "adjacency_matrix": [[0,1,0],[1,0,1],[0,1,0]],
            "directed": False, "review_status": "pending independent human review"}


class NormalizationTests(unittest.TestCase):
    def test_conservative_normalization(self):
        self.assertEqual(normalize_tag("  ａ – ７ "), "A-7")
        self.assertEqual(normalize_tag("a7"), "A7")
        self.assertNotEqual(normalize_tag("A7"), normalize_tag("A-7"))
        self.assertIsNone(normalize_tag("unreadable"))

    def test_reversed_duplicates_do_not_inflate(self):
        response = raw(edges=((0,1),(1,0),(1,2)))
        doc = normalize_response(response, DRAWING, RUN)
        self.assertEqual(len(doc["connections"]), 2)
        self.assertIn("duplicates/reverses", doc["warnings"][0])
        self.assertEqual(len(response["connections"]), 3)
        self.assertIs(validate_document(doc), doc)

    def test_reject_bad_id_tag_edges_and_missing_label(self):
        cases = []
        x = raw(); x["components"][1]["id"] = "o0"; cases.append(x)
        x = raw(); x["components"][1]["observed_tag"] = " a - 7 "; cases.append(x)
        x = raw(); x["connections"][0]["target"] = "absent"; cases.append(x)
        x = raw(); x["connections"][0]["target"] = "o0"; cases.append(x)
        x = raw(); del x["components"][0]["observed_tag"]; cases.append(x)
        x = raw(); x["connections"][0]["kind"] = "instrument_signal"; cases.append(x)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_response(value, DRAWING, RUN)

    def test_invalid_bboxes(self):
        for box in ([0,0,201,2], [3,0,2,4], [0,0,float("nan"),4], [False,0,3,4], [0,0,1]):
            value = raw(); value["components"][0]["bbox"] = box
            with self.subTest(box=box), self.assertRaises(ValidationError):
                normalize_response(value, DRAWING, RUN)

    def test_known_direction_requires_traced_path_and_in_bounds_arrow(self):
        response = raw()
        edge = response["connections"][0]
        edge["direction"] = {"status": "source_to_target", "arrow_tip": [70, 50],
                             "arrow_wings": [[65, 45], [65, 55]]}
        with self.assertRaisesRegex(ValidationError, "requires a traced path"):
            normalize_response(response, DRAWING, RUN)
        edge["path"] = [[35, 50], [130, 50]]
        edge["direction"]["arrow_tip"] = [200, 50]
        with self.assertRaisesRegex(ValidationError, "in-bounds arrow points"):
            normalize_response(response, DRAWING, RUN)

    def test_image_dimensions_and_metadata(self):
        value = raw(); value["image"]["width"] = 900
        with self.assertRaisesRegex(ValidationError, "do not rescale"):
            normalize_response(value, DRAWING, RUN)
        with self.assertRaisesRegex(ValidationError, "sha256"):
            normalize_response(raw(), {**DRAWING, "sha256": "invalid"}, RUN)

    def test_unresolved_retained(self):
        doc = normalize_response(raw(tags=("A-7", None, "C-9")), DRAWING, RUN)
        self.assertIsNone(doc["components"][1]["normalized_tag"])
        self.assertTrue(doc["warnings"])
        result = evaluate(doc, reference())
        self.assertEqual(result["unresolved"]["component_count"], 1)
        self.assertEqual(result["unresolved"]["connection_count"], 2)
        self.assertEqual(result["components"]["fn"], 1)

    def test_canonical_cannot_forge_normalized_tag(self):
        doc = normalize_response(raw(), DRAWING, RUN)
        doc["components"][0]["normalized_tag"] = "REPAIRED-BY-TRUTH"
        with self.assertRaisesRegex(ValidationError, "normalization"):
            validate_document(doc)

    def test_canonical_requires_provenance(self):
        doc = normalize_response(raw(), DRAWING, RUN)
        del doc["run"]["provider"]
        with self.assertRaisesRegex(ValidationError, "provider"):
            validate_document(doc)
        doc = normalize_response(raw(), DRAWING, RUN)
        doc["run"]["timestamp"] = "2026-11-10T12:00:00"
        with self.assertRaisesRegex(ValidationError, "UTC offset"):
            validate_document(doc)


class EvaluationTests(unittest.TestCase):
    def test_known_prediction_tests_evaluator_only(self):
        result = evaluate(normalize_response(raw(), DRAWING, RUN), reference())
        self.assertEqual(result["components"]["f1"], 1)
        self.assertEqual(result["connections"]["f1"], 1)
        self.assertEqual(result["scoring_status"], "provisional")

    def test_missing_extra_edge_counts(self):
        result = evaluate(normalize_response(raw(edges=((0,1),(0,2))), DRAWING, RUN), reference())
        self.assertEqual([result["connections"][k] for k in ("tp","fp","fn")], [1,1,1])
        self.assertEqual(result["connections"]["f1"], 0.5)
        self.assertEqual(result["connections"]["missing"], [["B-8","C-9"]])

    def test_wrong_tag_and_type_not_corrected(self):
        value = raw(tags=("A7","B-8","C-9")); value["components"][1]["type"] = "pump"
        result = evaluate(normalize_response(value, DRAWING, RUN), reference())
        self.assertIn("A7", result["components"]["extra"])
        self.assertIn("A-7", result["components"]["missing"])
        self.assertEqual(result["component_types"]["tp"], 1)

    def test_empty_predictions_defined_zero_recall_f1(self):
        doc = normalize_response(raw(tags=(), edges=()), DRAWING, RUN)
        result = evaluate(doc, reference())
        self.assertIsNone(result["components"]["precision"])
        self.assertEqual(result["components"]["recall"], 0)
        self.assertEqual(result["components"]["f1"], 0)
        ref = {"components": [], "edges": [], "directed": False}
        result = evaluate(doc, ref)
        for key in ("precision", "recall", "f1"):
            self.assertIsNone(result["components"][key])

    def test_bad_reference_matrix_and_endpoint_rejected(self):
        doc = normalize_response(raw(), DRAWING, RUN)
        ref = reference(); ref["adjacency_matrix"][0][0] = 1
        with self.assertRaisesRegex(ValidationError, "matrix"):
            evaluate(doc, ref)
        ref = reference(); ref["edges"][0]["source"] = "absent"
        with self.assertRaisesRegex(ValidationError, "dangling"):
            evaluate(doc, ref)
        ref = reference(); ref["image"]["sha256"] = ["invalid"]
        with self.assertRaisesRegex(ValidationError, "sha256"):
            evaluate(doc, ref)

    def test_input_binding_including_prepared_image_source(self):
        doc = normalize_response(raw(), {**DRAWING, "sha256": "b"*64}, RUN)
        with self.assertRaisesRegex(ValidationError, "SHA-256"):
            evaluate(doc, reference())
        doc["drawing"]["source_sha256"] = "a"*64
        self.assertEqual(evaluate(doc, reference())["components"]["f1"], 1)

    def test_hash_bound_drawing_id_mapping(self):
        doc = normalize_response(raw(), {**DRAWING, "drawing_id": "image-hash-derived-id"}, RUN)
        result = evaluate(doc, reference())
        self.assertEqual(result["reference_drawing_id"], "unit-fixture")
        self.assertIn("mapped by matching", result["warnings"][-1])
        ref = reference(); del ref["image"]
        with self.assertRaisesRegex(ValidationError, "drawing_id differ"):
            evaluate(doc, ref)


class ExportTests(unittest.TestCase):
    def test_flow_route_exports_coordinates_and_keeps_direct_adjacency(self):
        response = raw()
        response["arrows"] = [{"id": "arrow-001", "direction": "right", "arrow_tip": [110, 50],
                               "arrow_wings": [[100, 45], [100, 55]]}]
        response["flow_routes"] = [{"arrow_id": "arrow-001", "path": [[15, 50], [110, 50]],
                                    "component_ids": ["o0", "o1", "o2"]}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "source.png"
            Image.new("RGB", (200, 100), "white").save(image_path)
            drawing = {**DRAWING, "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}
            document = normalize_response(response, drawing, RUN)
            outputs = export_document(document, root / "exports", image_path)
            markings = json.loads(Path(outputs["markings.json"]).read_text(encoding="utf-8"))
            self.assertEqual(markings["arrows"], response["arrows"])
            self.assertEqual(markings["flow_routes"][0]["component_labels"], ["A-7", "B-8", "C-9"])
            marks = {mark["kind"]: mark for mark in markings["marks"]}
            self.assertEqual(marks["flow_route"]["points"], [[15, 50], [110, 50]])
            self.assertEqual(marks["observed_arrow"]["points"], [[100, 45], [110, 50], [100, 55]])
            self.assertEqual(marks["observed_arrow"]["route_id"], "arrow-001")
            with Path(outputs["adjacency.csv"]).open(newline="") as stream:
                self.assertEqual(list(csv.reader(stream))[1], ["A-7", "0", "1", "0"])
            with Image.open(outputs["overlay.png"]) as overlay:
                self.assertEqual(overlay.getpixel((70, 50)), (36, 97, 181))
        response["flow_routes"][0]["path"][-1] = [109, 50]
        with self.assertRaisesRegex(ValidationError, "ending at the observed arrow tip"):
            normalize_response(response, DRAWING, RUN)

    def test_markings_match_overlay_and_preserve_flow_when_endpoints_are_sorted(self):
        response = raw(edges=((1, 0), (1, 2)))
        response["components"][0].update(bbox=[10, 20, 40, 60], tag_bbox=[10, 10, 34, 17])
        response["connections"][0].update(
            path=[[140, 50], [39, 50]],
            direction={"status": "target_to_source", "arrow_tip": [100, 50],
                       "arrow_wings": [[92, 42], [92, 58]]},
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "source.png"
            Image.new("RGB", (200, 100), "white").save(image_path)
            drawing = {**DRAWING, "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}
            document = normalize_response(response, drawing, RUN)
            outputs = export_document(document, root / "exports", image_path)
            markings = json.loads(Path(outputs["markings.json"]).read_text(encoding="utf-8"))
            first, second = markings["connections"]
            self.assertEqual(first["path"], [[39, 50], [140, 50]])
            self.assertEqual(first["direction"]["status"], "source_to_target")
            self.assertEqual((first["direction"]["from"], first["direction"]["to"]), ("o0", "o1"))
            self.assertEqual(second["direction"]["status"], "unknown")
            self.assertIsNone(second["path"])
            by_kind = {mark["kind"]: mark for mark in markings["marks"]}
            self.assertEqual(by_kind["pipe_trace"]["points"], first["path"])
            self.assertEqual(by_kind["flow_arrow"]["points"], [[92, 42], [100, 50], [92, 58]])
            self.assertEqual(by_kind["tag_outline"]["bbox"], [10, 10, 34, 17])
            with Image.open(outputs["overlay.png"]) as overlay:
                self.assertEqual(overlay.getpixel((100, 50)), (18, 98, 79))
                self.assertEqual(overlay.getpixel((70, 50)), (8, 126, 139))
            self.assertEqual(response["connections"][0]["direction"]["status"], "target_to_source")

    def test_export_preserves_unresolved_ids_and_draws_only_evidence(self):
        value = raw(tags=("A-7", None, "C-9"))
        value["components"][0]["bbox"] = [10,20,40,60]
        doc = normalize_response(value, DRAWING, RUN)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (200,100), "white").save(root / "source.png")
            doc["drawing"]["sha256"] = hashlib.sha256((root / "source.png").read_bytes()).hexdigest()
            result = export_document(doc, root / "exports", root / "source.png")
            self.assertIn("overlay.png", result)
            with (root / "exports/adjacency.csv").open(newline="") as stream:
                matrix = list(csv.reader(stream))
            self.assertEqual(matrix[0], ["component_id", "A-7", "C-9", "unresolved:o1"])
            self.assertEqual(matrix[1], ["A-7", "0", "0", "1"])
            text = (root / "exports/review.txt").read_text()
            self.assertIn("Missing box observations (not drawn): o2, o1", text)
            with Image.open(root / "exports/overlay.png") as image:
                self.assertEqual(image.getpixel((39,59)), (176,0,32))

    def test_no_overlay_when_no_boxes(self):
        with tempfile.TemporaryDirectory() as temp:
            result = export_document(normalize_response(raw(), DRAWING, RUN), temp)
            self.assertNotIn("overlay.png", result)

    def test_wrong_image_rejected_before_writing_outputs(self):
        value = raw(); value["components"][0]["bbox"] = [10,20,40,60]
        doc = normalize_response(value, DRAWING, RUN)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (200,100), "white").save(root / "wrong.png")
            with self.assertRaisesRegex(ValidationError, "SHA-256 differs"):
                export_document(doc, root / "exports", root / "wrong.png")
            self.assertFalse((root / "exports").exists())

    def test_stale_overlay_cannot_misrepresent_unboxed_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "overlay.png").write_bytes(b"previous-overlay")
            with self.assertRaisesRegex(ValidationError, "previous overlay"):
                export_document(normalize_response(raw(), DRAWING, RUN), root)
            self.assertEqual((root / "overlay.png").read_bytes(), b"previous-overlay")

    def test_source_overlay_uses_prepared_exif_and_transparency_transform(self):
        value = raw(); value["components"][0]["bbox"] = [10,20,40,60]
        doc = normalize_response(value, DRAWING, RUN)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = Image.new("RGBA", (200,100), (0,0,0,0))
            source.putpixel((0,0), (255,0,0,255))
            exif = Image.Exif(); exif[274] = 3
            source.save(root / "original.png", exif=exif)
            doc["drawing"]["source_sha256"] = hashlib.sha256((root / "original.png").read_bytes()).hexdigest()
            export_document(doc, root / "exports", root / "original.png")
            with Image.open(root / "exports/overlay.png") as overlay:
                self.assertEqual(overlay.getpixel((199,99)), (255,0,0))
                self.assertEqual(overlay.getpixel((199,0)), (255,255,255))


if __name__ == "__main__":
    unittest.main()
