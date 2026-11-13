"""CLI-level evidence preservation and assisted-workflow status tests."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from aip.cli import main
from aip.io import prepare_input


ROOT = Path(__file__).resolve().parents[1]


class AssistedCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.packet = self.root / "packet"
        self.runs = self.root / "runs"
        image = Image.new("RGB", (300, 200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 40, 80, 100), outline="black", width=3)
        draw.ellipse((150, 40, 200, 100), outline="black", width=3)
        draw.line((80, 70, 150, 70), fill="black", width=3)
        source = self.root / "generic.png"
        image.save(source)
        prompt = self.root / "prompt.txt"
        prompt.write_text("Interpret visible process equipment and direct undirected contacts.", encoding="utf-8")
        prepare_input(source, self.packet, prompt)

    def tearDown(self):
        self.temp.cleanup()

    def response(self):
        # A plumbing fixture, not a claimed live model result or the real drawing.
        return {
            "image": {"width": 300, "height": 200},
            "components": [
                {"id": "left", "observed_tag": " vessel - 42 ", "type": "tank", "bbox": [30, 40, 81, 101], "evidence": "test fixture", "uncertainty": None},
                {"id": "right", "observed_tag": "MACHINE-B", "type": "pump", "bbox": [150, 40, 201, 101], "evidence": "test fixture", "uncertainty": None},
            ],
            "connections": [{"source": "left", "target": "right", "kind": "direct_undirected", "evidence": "test fixture line", "uncertainty": None}],
            "uncertainties": [],
        }

    def invoke(self, args):
        before = set(self.runs.iterdir()) if self.runs.exists() else set()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args)
        after = set(self.runs.iterdir()) if self.runs.exists() else set()
        created = after-before
        self.assertEqual(len(created), 1, (stdout.getvalue(), stderr.getvalue()))
        return code, created.pop(), stdout.getvalue(), stderr.getvalue()

    def import_response(self, source, stage="original", model="test-fixture-model"):
        return self.invoke([
            "import-response", str(source), "--packet", str(self.packet),
            "--runs", str(self.runs), "--model", model,
            "--provider", "unit-test fixture, not live inference", "--stage", stage,
        ])

    def test_malformed_json_preserved_with_failure_separate_from_metrics(self):
        source = self.root / "malformed.txt"
        raw = b'```json\r\n{"components": [BROKEN}\r\n```\r\n'
        source.write_bytes(raw)
        code, run, _, stderr = self.import_response(source)
        self.assertEqual(code, 2)
        self.assertIn("Run failed", stderr)
        self.assertEqual((run / "raw_response.txt").read_bytes(), raw)
        failure = json.loads((run / "failure.json").read_text())
        self.assertTrue(failure["raw_response_preserved"])
        self.assertFalse(failure["scored"])
        self.assertIsNone(failure["metrics"])
        self.assertFalse((run / "prediction.json").exists())
        self.assertEqual(json.loads((run / "run.json").read_text())["status"], "failed_validation_or_inference")

    def test_dangling_endpoint_preserved_and_not_exported(self):
        response = self.response()
        response["connections"][0]["target"] = "nonexistent-observation"
        source = self.root / "dangling.json"
        raw = json.dumps(response, indent=3).encode("utf-8")
        source.write_bytes(raw)
        code, run, _, _ = self.import_response(source)
        self.assertEqual(code, 2)
        self.assertEqual((run / "raw_response.txt").read_bytes(), raw)
        failure = json.loads((run / "failure.json").read_text())
        self.assertIn("does not name an observed component", failure["error"])
        self.assertIsNone(failure["metrics"])
        self.assertFalse((run / "prediction.json").exists())
        self.assertFalse((run / "exports").exists())

    def test_valid_import_normalizes_observation_and_entry_point_validates(self):
        source = self.root / "valid.json"
        source.write_text(json.dumps(self.response()), encoding="utf-8")
        code, run, _, stderr = self.import_response(source)
        self.assertEqual(code, 0, stderr)
        document = json.loads((run / "prediction.json").read_text())
        self.assertEqual(document["components"][0]["observed_tag"], " vessel - 42 ")
        self.assertEqual(document["components"][0]["normalized_tag"], "VESSEL-42")
        self.assertEqual(document["run"]["model"], "test-fixture-model")
        self.assertEqual((run / "raw_response.txt").read_bytes(), source.read_bytes())
        self.assertTrue((run / "exports/adjacency.csv").is_file())
        self.assertTrue((run / "exports/overlay.png").is_file())
        result = subprocess.run(
            [sys.executable, "-s", str(ROOT / "scripts/aip.py"), "validate", str(run / "prediction.json")],
            cwd=self.root, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Valid schema", result.stdout)

    def test_reviewed_import_creates_separate_run_and_preserves_original(self):
        source = self.root / "original.json"
        source.write_text(json.dumps(self.response()), encoding="utf-8")
        code, original, _, _ = self.import_response(source)
        self.assertEqual(code, 0)
        original_bytes = (original / "prediction.json").read_bytes()
        corrected = self.response()
        corrected["components"][0]["type"] = "unknown"
        corrected["components"][0]["uncertainty"] = "Reviewed test fixture correction"
        review_source = self.root / "reviewed.json"
        review_source.write_text(json.dumps(corrected), encoding="utf-8")
        code, reviewed, _, stderr = self.import_response(review_source, stage="reviewed")
        self.assertEqual(code, 0, stderr)
        self.assertNotEqual(original, reviewed)
        self.assertEqual((original / "prediction.json").read_bytes(), original_bytes)
        self.assertEqual(json.loads((original / "run.json").read_text())["stage"], "original")
        self.assertEqual(json.loads((reviewed / "run.json").read_text())["stage"], "reviewed")
        self.assertNotEqual((original / "raw_response.txt").read_bytes(), (reviewed / "raw_response.txt").read_bytes())

    def test_unavailable_interactive_route_is_explicit_exit_three(self):
        code, run, _, _ = self.invoke([
            "extract", "--packet", str(self.packet), "--runs", str(self.runs),
            "--method", "interactive-manual",
        ])
        self.assertEqual(code, 3)
        metadata = json.loads((run / "run.json").read_text())
        self.assertEqual(metadata["status"], "awaiting_manual_response")
        self.assertTrue(metadata["manual_stages"])
        self.assertTrue((run / "manual_handoff.json").is_file())
        self.assertFalse((run / "prediction.json").exists())
        self.assertFalse((run / "evaluation.json").exists())


if __name__ == "__main__":
    unittest.main()
