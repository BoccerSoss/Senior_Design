import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aip.v3.scoring import micro_average, score_graph

script = Path(__file__).resolve().parents[1] / "scripts/compare_v3.py"
spec = importlib.util.spec_from_file_location("compare_v3", script)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def graph(ids, tags, edges):
    components = []
    for i, component_id in enumerate(ids):
        item = {"id": component_id, "type": "valve", "bbox": [30 * i, 0, 30 * i + 10, 10]}
        if tags is not None:
            item["tag"] = tags[i]
        components.append(item)
    return {"components": components,
            "connections": [{"source": ids[a], "target": ids[b], "kind": "pipe"} for a, b in edges]}


class V3ScoringTests(unittest.TestCase):
    def test_benchmark_explicitly_disables_geometry_lookup(self):
        with patch.object(benchmark, "analyze_drawing", return_value=SimpleNamespace(reference_citation=None)) as run, \
             patch.object(benchmark, "analysis_objects", return_value=([], [], {"method": "classical"})):
            benchmark._baseline(Path("image.png"), Path("out"))
        run.assert_called_once_with(Path("image.png"), Path("out"), use_reference=False)
        with patch.object(benchmark, "analyze_drawing", return_value=SimpleNamespace(reference_citation="source")), \
             patch.object(benchmark, "analysis_objects", return_value=([], [], {"method": "drawing-reference"})):
            with self.assertRaisesRegex(ValueError, "Source-assisted"):
                benchmark._baseline(Path("image.png"), Path("out"))

    def test_images_cannot_be_substituted_or_counted_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "page.png").write_bytes(b"abc")
            entry = {"id": "a", "image": "page.png", "image_sha256": hashlib.sha256(b"abc").hexdigest()}
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                benchmark.validate_cases({"sheets": [entry, {**entry, "id": "b"}]}, root)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                benchmark.validate_cases({"sheets": [{**entry, "image_sha256": "0"*64}]}, root)

    def test_normalized_tags_and_reversed_edges_without_id_leakage(self):
        actual = graph(["1", "2"], ["v – 1", "p-2"], [(0, 1), (1, 0)])
        reference = graph(["R8", "R7"], ["V-1", "P-2"], [(1, 0)])
        result, _ = score_graph(actual, reference)
        self.assertEqual(result["named_edges"]["tp"], 1)
        self.assertEqual(result["named_edges"]["fp"], 0)
        self.assertEqual(result["localized_edges"]["f1"], 1)

    def test_wrong_tag_and_missed_location_each_count_as_error(self):
        actual = graph(["1", "2", "3"], ["X", "B", "C"], [(0, 1), (1, 2)])
        ref = graph(["A", "B", "C"], ["A", "B", "C"], [(0, 1), (1, 2)])
        actual["components"][2]["bbox"] = [200, 20, 220, 40]
        result, detail = score_graph(actual, ref)
        self.assertEqual(result["named_edges"]["f1"], 0)
        self.assertEqual(result["named_edges"]["fp"], 2)
        self.assertEqual(result["named_edges"]["fn"], 2)
        self.assertEqual(result["localized_edges"]["f1"], .5)
        self.assertEqual(result["endpoint_coverage"], {"reference_pairs": 2, "both_endpoints_located": 1,
                                                      "both_endpoints_located_and_named": 0})
        self.assertEqual(detail["matched_nodes"], {"1": "A", "2": "B"})

    def test_unprinted_reference_names_and_absent_transcriptions_differ(self):
        ref = graph(["A", "B"], [None, "V-11"], [(0, 1)])
        actual = graph(["1", "2"], [None, "V-11"], [(0, 1)])
        result, _ = score_graph(actual, ref)
        self.assertEqual(result["named_edges"]["f1"], 1)
        actual["components"][0]["tag"] = "invented name"
        self.assertEqual(score_graph(actual, ref)[0]["named_edges"]["f1"], 0)
        del ref["components"][0]["tag"]
        result, _ = score_graph(actual, ref)
        self.assertIsNone(result["named_edges"])
        self.assertEqual(result["localized_edges"]["f1"], 1)

    def test_false_edge_sharing_one_localized_symbol_and_duplicated_symbol(self):
        truth = graph(["A", "B"], ["A", "B"], [(0, 1)])
        actual = graph(["1", "2", "3"], ["A", "B", "B"], [(0, 1), (0, 2)])
        actual["components"][2]["bbox"] = actual["components"][1]["bbox"]
        counts = score_graph(actual, truth)[0]["localized_edges"]
        self.assertEqual((counts["tp"], counts["fp"], counts["fn"]), (1, 1, 0))

    def test_signal_and_junction_do_not_create_shortcuts(self):
        ref = graph(["A", "B", "tee"], ["P-1", "V-2", None], [(0, 1), (1, 2)])
        ref["components"][2]["type"] = "junction"
        ref["connections"][0]["kind"] = "signal"
        result, _ = score_graph(ref, ref)
        self.assertEqual(result["localized_nodes"]["tp"], 2)
        self.assertIsNone(result["localized_edges"]["f1"])

    def test_micro_counts_pool_across_sheets_and_preserve_missing_runs(self):
        many = graph(["A", "B", "C"], ["A", "B", "C"], [(0, 1), (1, 2)])
        one = graph(["A", "B"], ["A", "B"], [(0, 1)])
        missed = graph([], [], [])
        scored = [score_graph(many, many)[0], score_graph(missed, one)[0]]
        result = micro_average(scored)["named_edges"]
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (2, 0, 1))
        self.assertEqual(result["f1"], .8)
        self.assertAlmostEqual(result["gap_to_target"], .1)
        self.assertIsNone(micro_average([*scored, None])["named_edges"])
