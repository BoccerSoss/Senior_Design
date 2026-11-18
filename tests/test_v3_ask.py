import json
import os
import unittest
from unittest.mock import patch

from aip.v2.matrix import build_matrix
from aip.v2.providers import ProviderError
from aip.v3.ask import ask_drawing


class GroundedQuestions(unittest.TestCase):
    def setUp(self):
        self.sheet = {
            "id": "drawing", "facility_id": "demo", "drawing_number": "Figure A",
            "width": 100, "height": 100, "source_metadata": {"secret": "PRIVATE_NOTE"},
            "components": [{"id": "p", "tag": "P-4", "type": "pump", "bbox": [1, 1, 5, 5]},
                           {"id": "v", "tag": "V-8", "type": "valve", "bbox": [11, 1, 15, 5]},
                           {"id": "v2", "tag": "V-80", "type": "valve", "bbox": [21, 1, 25, 5]}],
            "connections": [{"id": "e", "source": "v", "target": "p", "kind": "pipe",
                             "direction": "source_to_target"}],
        }
        self.matrix = build_matrix({"facilities": [{"id": "demo"}], "sheets": [self.sheet]}, "demo", "drawing")
        self.local = {"provider": "local-graph"}
        self.cloud = {"provider": "openai", "model": "selected-model"}

    def ask(self, question, settings=None):
        return ask_drawing(settings or self.local, question, self.sheet, self.matrix)

    def test_local_has_exact_edge_evidence_and_preserves_direction(self):
        with patch("aip.v3.ask._generate", side_effect=AssertionError):
            answer = self.ask("What connects to V-8?")
        self.assertEqual(["e"], [item["id"] for item in answer["citations"]])
        self.assertIn("from V-8 to P-4", answer["answer"])
        self.assertIn("unreviewed", answer["answer"])
        self.assertNotIn("V-80", answer["answer"])

    def test_unrelated_or_operational_question_abstains(self):
        self.assertEqual([], self.ask("Can I close V-8?")["citations"])
        self.assertEqual([], self.ask("Which is nearest the river?")["citations"])

    def test_exact_tag_pair_without_a_recorded_connection(self):
        answer = self.ask("Are V-80 and P-4 directly connected?")
        self.assertIn("no direct connection between P-4 and V-80", answer["answer"])
        self.assertEqual("sheet", answer["citations"][0]["kind"])

    def test_readonly_cloud_uses_sheet_records_and_renders_cited_facts_only(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}, clear=True), \
                patch("aip.v3.ask._generate", return_value='{"references":["S1"],"answer":"invented"}') as generate:
            answer = self.ask("How many connections are recorded?", self.cloud)
        context = json.loads(generate.call_args.args[2])
        self.assertEqual("How many connections are recorded?", context["question"])
        self.assertNotIn("PRIVATE_NOTE", str(context))
        self.assertNotIn("invented", answer["answer"])
        self.assertEqual("drawing", answer["citations"][0]["sheet_id"])
        self.assertEqual("selected-model", answer["model"])

    def test_rejects_fictional_citations_and_unsupported_prose(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}, clear=True):
            for text in ('{"references":["V-8"]}', "This question is easy.", '{"references":[{}]}'):
                with self.subTest(text=text), patch("aip.v3.ask._generate", return_value=text), self.assertRaises(ProviderError):
                    self.ask("What connects to V-8?", self.cloud)

    def test_selected_matrix_must_match_drawing(self):
        self.matrix["scope"]["sheet_id"] = "other"
        with self.assertRaisesRegex(ValueError, "same sheet"):
            self.ask("Which equipment?")

    def test_question_about_coordinates_returns_grid_evidence(self):
        answer = self.ask("Where is V-80 on the grid?")
        self.assertIn("Grid (3, 1)", answer["answer"])
        self.assertEqual("v2", answer["citations"][0]["id"])
