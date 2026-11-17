"""Grid independence and persistent correction identity regressions."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from aip.v2.grid import equipment_positions, grid_settings
from aip.v2.store import Conflict, Store


def component(item_id, x, tag=""):
    return {"id": item_id, "tag": tag, "type": "pump", "bbox": [x, 10, x+10, 20]}


class StoreGridTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.items = [component("a", 10, "P-A"), component("b", 70, "P-B")]
        self.edges = [{"id": "e", "source": "a", "target": "b", "kind": "pipe", "path": [[20,15],[70,15]]}]
        self.store.add_sheet({"id": "sheet", "facility_id": "facility-local", "filename": "test.png",
            "title": "Test", "drawing_number": "T-1", "sheet_number": "1", "revision": "A",
            "width": 100, "height": 100, "components": [], "connections": [], "source_metadata": {}})
        self.store.replace_analysis("sheet", self.items, self.edges, {"method": "test"})

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_grid_change_does_not_change_graph_or_corrections(self):
        before = self.store.sheet("sheet")
        after = self.store.update_sheet("sheet", {"grid": {"columns": 2, "rows": 2}})
        self.assertEqual(before["components"], after["components"])
        self.assertEqual(before["connections"], after["connections"])
        self.assertEqual(before["corrections"], after["corrections"])
        self.assertEqual([[1,1], [2,1]], [p["coordinate"] for p in equipment_positions(after)["coordinates"]])
        shared = equipment_positions({**after, "grid": {"columns": 1, "rows": 1}})
        self.assertEqual([[1,1], [1,1]], [p["coordinate"] for p in shared["coordinates"]])

    def test_grid_bounds_and_missing_symbol_position(self):
        for value in (0, 201, True, 2.5):
            with self.assertRaises(ValueError):
                grid_settings({"columns": value})
        result = equipment_positions({"id": "s", "width": 100, "height": 100,
                                      "components": [{"id": "x", "bbox": None}]})
        self.assertIsNone(result["coordinates"][0]["coordinate"])

    def test_correction_follows_geometry_when_detection_ids_swap(self):
        self.store.edit_item("sheet", "components", {"id": "a", "tag": "Reviewed pump", "review_status": "confirmed"})
        changed = [component("a", 70, "P-B"), component("b", 10, "P-A")]
        outcome = self.store.replace_analysis("sheet", changed, self.edges, {"method": "rerun"})
        corrected = next(c for c in outcome["sheet"]["components"] if c["tag"] == "Reviewed pump")
        self.assertEqual([10,10,20,20], corrected["bbox"])
        self.assertEqual("a", corrected["id"])
        self.assertEqual(1, outcome["corrections_retained"])

    def test_missing_observation_leaves_correction_pending(self):
        self.store.edit_item("sheet", "components", {"id": "a", "tag": "Reviewed pump"})
        outcome = self.store.replace_analysis("sheet", [component("a", 40)], [], {"method": "changed"})
        self.assertEqual(["a"], outcome["pending"])
        self.assertNotIn("Reviewed pump", [c["tag"] for c in outcome["sheet"]["components"]])

    def test_pending_correction_reattaches_when_observation_returns(self):
        self.store.edit_item("sheet", "components", {"id": "a", "tag": "Reviewed pump",
                                                       "review_status": "confirmed"})
        missing = self.store.replace_analysis("sheet", [self.items[1]], [], {"method": "dropout"})
        self.assertEqual(["a"], missing["pending"])
        recovered = self.store.replace_analysis("sheet", self.items, self.edges, {"method": "recovered"})
        self.assertEqual([], recovered["pending"])
        corrected = next(c for c in recovered["sheet"]["components"] if c["id"] == "a")
        self.assertEqual("Reviewed pump", corrected["tag"])
        self.assertEqual("confirmed", corrected["review_status"])

    def test_user_added_component_survives_rerun(self):
        self.store.edit_item("sheet", "components", component("manual", 40, "Added"))
        outcome = self.store.replace_analysis("sheet", self.items, self.edges, {})
        self.assertIn("manual", [c["id"] for c in outcome["sheet"]["components"]])

    def test_connection_replay_waits_for_manually_added_components(self):
        self.store.edit_item("sheet", "components", component("manual", 40, "Added"))
        self.store.edit_item("sheet", "connections", {"id":"manual-edge", "source":"a", "target":"manual", "kind":"pipe"})
        self.store.edit_item("sheet", "components", {"id":"manual", "tag":"Changed after edge"})
        outcome = self.store.replace_analysis("sheet", self.items, self.edges, {})
        self.assertEqual([], outcome["pending"])
        self.assertIn("manual-edge", [edge["id"] for edge in outcome["sheet"]["connections"]])

    def test_later_detection_merges_with_user_added_component(self):
        added = component("manual", 40, "Added by reviewer")
        self.store.edit_item("sheet", "components", added)
        detected = component("new-observation", 40, "Newly detected label")
        outcome = self.store.replace_analysis("sheet", self.items + [detected], self.edges, {})
        at_position = [c for c in outcome["sheet"]["components"] if c["bbox"] == added["bbox"]]
        self.assertEqual(1, len(at_position))
        self.assertEqual("manual", at_position[0]["id"])
        self.assertEqual("Added by reviewer", at_position[0]["tag"])
        self.assertEqual([], outcome["pending"])

    def test_parallel_pipe_paths_keep_correction_on_matching_geometry(self):
        self.store.edit_item("sheet", "connections", {"id": "e", "direction": "source_to_target",
                                                        "review_status": "confirmed"})
        lower_path = [[20, 15], [20, 80], [70, 80], [70, 15]]
        changed = [{**self.edges[0], "id": "e", "path": lower_path},
                   {**self.edges[0], "id": "new-upper"}]
        outcome = self.store.replace_analysis("sheet", self.items, changed, {"method": "parallel"})
        edges = outcome["sheet"]["connections"]
        self.assertEqual(2, len(edges))
        upper = [edge for edge in edges if edge["path"] == self.edges[0]["path"]]
        lower = [edge for edge in edges if edge["path"] == lower_path]
        self.assertEqual(1, len(upper))
        self.assertEqual(1, len(lower))
        self.assertEqual("e", upper[0]["id"])
        self.assertEqual("source_to_target", upper[0]["direction"])
        self.assertEqual("confirmed", upper[0]["review_status"])
        self.assertNotEqual("e", lower[0]["id"])
        self.assertEqual("unreviewed", lower[0]["review_status"])

    def test_deleted_component_and_incident_edges_do_not_reappear(self):
        self.store.edit_item("sheet", "components", delete_id="a")
        outcome = self.store.replace_analysis("sheet", self.items, self.edges, {})
        self.assertEqual(["b"], [c["id"] for c in outcome["sheet"]["components"]])
        self.assertEqual([], outcome["sheet"]["connections"])

    def test_conflict_and_invalid_analysis_are_transactional(self):
        version = self.store.sheet("sheet")["version"]
        self.store.update_sheet("sheet", {"title": "Changed"}, version)
        with self.assertRaises(Conflict):
            self.store.update_sheet("sheet", {"title": "Stale"}, version)
        before = self.store.snapshot()
        for components, edges in ((self.items*2, []), (self.items, [{**self.edges[0], "target": "missing"}])):
            with self.assertRaises(ValueError):
                self.store.replace_analysis("sheet", components, edges, {})
        self.assertEqual(before, self.store.snapshot())

    def test_secret_fields_and_urls_are_rejected_before_persistence(self):
        for patch in ({"api_key": "secret"}, {"base_url": "https://host/path?key=secret"},
                      {"base_url": "https://user:secret@host/v1"}):
            with self.assertRaises(ValueError):
                self.store.update_settings(patch)
        self.assertEqual("", self.store.snapshot()["settings"]["base_url"])
        self.assertNotIn("api_key", self.store.snapshot())

    def test_state_survives_reopen_and_reports_interrupted_job(self):
        job = self.store.job("Read", "facility-local")
        self.store.update_job(job["id"], status="running")
        second = Store(self.temp.name)
        try:
            self.assertEqual("interrupted", second.snapshot()["jobs"][0]["status"])
            self.assertEqual(2, len(second.sheet("sheet")["components"]))
        finally:
            second.db.close()


if __name__ == "__main__":
    unittest.main()
