import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from aip.v2.store import Store
from aip.v3.beginner import COLLECTION, SOURCE, preload_beginner
from aip.v3.server import Application


class BeginnerDrawings(unittest.TestCase):
    def test_read_with_symbol_reference_requires_matching_png_and_leaves_global_method_alone(self):
        with TemporaryDirectory() as directory:
            app = Application(directory)
            try:
                sheet = app.state()["sheets"][0]
                self.assertTrue(sheet["has_symbol_reference"])
                request = {"action": "read_symbol_reference", "sheet_id": sheet["id"]}
                with patch.object(app.imports, "reanalyze", return_value={"id": "test-job"}) as read:
                    self.assertEqual("test-job", app.action(request)["job"]["id"])
                    read.assert_called_once_with(sheet["id"], require_reference=True)
                    png = Path(directory) / "images" / (sheet["id"] + ".png")
                    Image.new("RGB", (sheet["width"], sheet["height"]), "white").save(png)
                    self.assertFalse(app.has_symbol_reference(sheet))
                    with self.assertRaisesRegex(ValueError, "no matching symbol reference"):
                        app.action(request)
                    read.assert_called_once()
                self.assertFalse(app.store.snapshot()["settings"]["use_geometry_reference"])
            finally:
                app.close()
                app.store.db.close()

    def test_fresh_launch_and_demo_button_use_all_six_source_pngs_without_analysis(self):
        with TemporaryDirectory() as directory, patch("aip.v2.ingest.Imports.submit") as submit:
            app = Application(directory)
            try:
                opened = app.action({"action": "demo", "kind": "beginner"})["result"]
                state = app.store.snapshot()
                self.assertEqual(opened["facility_id"], state["facilities"][0]["id"])
                self.assertEqual(opened["sheet_ids"], [sheet["id"] for sheet in state["sheets"]])
                self.assertEqual(list(range(65, 71)), [sheet["source_metadata"]["source_pdf_page"]
                                                      for sheet in state["sheets"]])
                for sheet in state["sheets"]:
                    png = Path(directory) / "images" / (sheet["id"] + ".png")
                    self.assertEqual(sheet["source_sha256"], hashlib.sha256(png.read_bytes()).hexdigest())
                    with Image.open(png) as image:
                        self.assertEqual("PNG", image.format)
                        self.assertEqual((sheet["width"], sheet["height"]), image.size)
                    self.assertEqual([], sheet["components"])
                self.assertEqual([], state["jobs"])
                submit.assert_not_called()
                self.assertEqual(opened, preload_beginner(app.store))
                self.assertEqual(state, app.store.snapshot())
            finally:
                app.close()
                app.store.db.close()

    def test_upgrade_preserves_earlier_imports_corrections_and_collections(self):
        with TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                facility = store.add_facility(COLLECTION)
                record = json.loads((SOURCE / "source.json").read_text(encoding="utf-8"))["pages"][0]
                store.add_sheet({"id": "legacy", "facility_id": facility["id"], "title": "Custom page name",
                                 "source_page": 1, "source_sha256": record["single_page_pdf_sha256"],
                                 "connections": [], "components": [], "corrections": [{"id": "saved-edit"}]})
                original = store.sheet("legacy")
                original_facilities = store.snapshot()["facilities"]
                opened = preload_beginner(store)
                self.assertEqual("legacy", opened["sheet_ids"][0])
                self.assertEqual(6, len(store.snapshot()["sheets"]))
                self.assertEqual(original, store.sheet("legacy"))
                self.assertEqual(original_facilities, store.snapshot()["facilities"])
                # An interrupted file copy is repairable without losing its existing record.
                second = opened["sheet_ids"][1]
                before = store.sheet(second)
                missing = Path(directory) / "images" / (second + ".png")
                missing.unlink()
                self.assertEqual(opened, preload_beginner(store))
                self.assertEqual(before, store.sheet(second))
                self.assertEqual(before["source_sha256"], hashlib.sha256(missing.read_bytes()).hexdigest())
            finally:
                store.db.close()
