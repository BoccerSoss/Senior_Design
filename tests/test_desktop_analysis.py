"""Desktop service regression checks with simulated pipeline exports."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from aip.desktop_analysis import AnalysisError, analyze_drawing
from aip.io import ROOT, digest, write_json


def fixture_run(packet, runs_dir, *, method):
    if method != "classical" or not (packet / "image.png").is_file():
        raise AssertionError("The service must call the classical pipeline with a prepared image.")
    run = runs_dir / "mock-extraction"
    exports = run / "exports"
    exports.mkdir(parents=True)
    (exports / "adjacency.csv").write_text(
        "component_id,EQ-7,unresolved:obs-2\nEQ-7,0,1\nunresolved:obs-2,1,0\n", encoding="utf-8",
    )
    (exports / "equipment.csv").write_text(
        "id,type,matrix_label\nobs-1,tank,EQ-7\nobs-2,unknown,unresolved:obs-2\n", encoding="utf-8",
    )
    (exports / "edges.csv").write_text("source,target\nobs-1,obs-2\n", encoding="utf-8")
    return run, 0


class DesktopAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.custom = self.root / "custom.png"
        Image.new("RGB", (80, 60), "white").save(self.custom)
        self.output = self.root / "analysis"

    def test_prepared_classical_runs_preserve_exports_and_unresolved_labels(self):
        with patch("aip.desktop_analysis.process_run", side_effect=fixture_run) as run, \
             patch("aip.desktop_analysis.evaluate_files") as score:
            first = analyze_drawing(self.custom, self.output)
            second = analyze_drawing(self.custom, self.output)
        self.assertEqual(run.call_count, 2)
        score.assert_not_called()
        self.assertNotEqual(first.directory, second.directory)
        self.assertTrue(first.image.is_file())
        self.assertTrue(first.matrix_file.is_file())
        self.assertEqual(first.equipment, [("EQ-7", "tank"), ("unresolved:obs-2", "unknown")])
        self.assertEqual(first.connections, [("EQ-7", "unresolved:obs-2")])
        self.assertEqual(first.matrix_labels, ["EQ-7", "unresolved:obs-2"])
        self.assertEqual(first.matrix, [[0, 1], [1, 0]])
        self.assertIsNone(first.evaluation)

    def test_only_a_byte_identical_copy_of_the_bundled_drawing_receives_a_score(self):
        renamed = self.root / "renamed.png"
        shutil.copyfile(ROOT / "data" / "input" / "four_component.png", renamed)
        reencoded = self.root / "reencoded.png"
        with Image.open(renamed) as image:
            info = PngImagePlugin.PngInfo()
            info.add_text("test", "same pixels, different source bytes")
            image.save(reencoded, pnginfo=info)
        report = {"scoring_status": "provisional"}
        with patch("aip.desktop_analysis.process_run", side_effect=fixture_run), \
             patch("aip.desktop_analysis.evaluate_files", return_value=report) as score:
            result = analyze_drawing(renamed, self.output)
            custom = analyze_drawing(reencoded, self.output)
        score.assert_called_once_with(result.directory / "prediction.json",
                                      ROOT / "data" / "reference" / "four_component.reference.json",
                                      result.directory / "evaluation.json")
        self.assertEqual(result.evaluation, report)
        self.assertNotEqual(digest(renamed), digest(reencoded))
        self.assertEqual(digest(result.image), digest(custom.image))
        self.assertIsNone(custom.evaluation)

    def test_mismatched_matrix_labels_produce_a_preserved_diagnostic(self):
        def inconsistent_run(*args, **kwargs):
            run, code = fixture_run(*args, **kwargs)
            (run / "exports" / "adjacency.csv").write_text(
                "component_id,EQ-7,unresolved:obs-2\nunresolved:obs-2,1,0\nEQ-7,0,1\n", encoding="utf-8",
            )
            return run, code

        with patch("aip.desktop_analysis.process_run", side_effect=inconsistent_run):
            with self.assertRaisesRegex(AnalysisError, "row labels") as caught:
                analyze_drawing(self.custom, self.output)
        self.assertTrue((caught.exception.directory / "desktop_failure.json").is_file())

    def test_pipeline_failure_exposes_diagnostic_and_existing_run(self):
        def failed_run(packet, runs_dir, *, method):
            run = runs_dir / "failed-extraction"
            run.mkdir(parents=True)
            write_json(run / "failure.json", {"error": "OCR worker failed."})
            (run / "worker_stderr.txt").write_text("OCRUnavailable: No installed OCR language.\n", encoding="utf-8")
            return run, 2

        with patch("aip.desktop_analysis.process_run", side_effect=failed_run):
            with self.assertRaisesRegex(AnalysisError, "No installed OCR language") as caught:
                analyze_drawing(self.custom, self.output)
        self.assertTrue((caught.exception.directory / "failure.json").is_file())


if __name__ == "__main__":
    unittest.main()
