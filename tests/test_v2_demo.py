from __future__ import annotations

import tempfile
import unittest

from PIL import Image

from aip.v2.demo import create_demo
from aip.v2.network import scope_isolation, trace_network
from aip.v2.store import Store


class SyntheticDemoTests(unittest.TestCase):
    def test_demo_persists_two_actual_drawings_and_exercises_cross_sheet_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            result = create_demo(store)
            state = store.snapshot()
            self.assertEqual(len(result["sheet_ids"]), 2)
            self.assertTrue(result["synthetic"])
            self.assertFalse(result["duplicate"])
            for sheet_id in result["sheet_ids"]:
                with Image.open(store.directory / "images" / f"{sheet_id}.png") as image:
                    self.assertEqual(image.size, (1400, 850))
                    self.assertLess(len(image.getcolors(image.width * image.height)), 5000)
                sheet = store.sheet(sheet_id)
                ids = {node["id"] for node in sheet["components"]}
                self.assertTrue(all(edge["source"] in ids and edge["target"] in ids for edge in sheet["connections"]))
                for item in sheet["components"]:
                    Store._validate_item(item, sheet, "components")
                for edge in sheet["connections"]:
                    Store._validate_item(edge, sheet, "connections")
            target = result["target"]
            isolation = scope_isolation(state, result["facility_id"], **target)
            self.assertEqual({node["sheet_id"] for node in isolation["nodes"]}, set(result["sheet_ids"]))
            self.assertEqual({node["tag"] for node in isolation["closures"]}, {"V-101", "V-102", "V-201", "V-202"})
            self.assertEqual(isolation["status"], "incomplete")
            self.assertEqual([node["tag"] for node in isolation["drains"]], ["DV-101"])
            self.assertEqual([node["tag"] for node in isolation["vents"]], ["AV-101"])
            self.assertEqual([node["tag"] for node in isolation["relief"]], ["RV-101"])
            self.assertIn("CV-201", {node["tag"] for node in isolation["not_credited"]})
            ordinary = trace_network(state, result["facility_id"], **target)
            with_signals = trace_network(state, result["facility_id"], include_signals=True, **target)
            self.assertNotIn("PI-101", {node["tag"] for node in ordinary["nodes"]})
            self.assertIn("PI-101", {node["tag"] for node in with_signals["nodes"]})
            again = create_demo(store)
            self.assertTrue(again["duplicate"])
            self.assertEqual(again["sheet_ids"], result["sheet_ids"])
            self.assertEqual(len(store.snapshot()["sheets"]), 2)
            self.assertEqual(len(store.snapshot()["links"]), 1)
            store.db.close()


if __name__ == "__main__":
    unittest.main()
