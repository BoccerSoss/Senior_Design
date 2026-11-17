"""Conversion and failure reporting at the import boundary."""
from io import BytesIO
from pathlib import Path
import json
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from aip.v2.ingest import Imports, _image_frames, unpack, analysis_objects
from aip.v2.store import Store


def png():
    out = BytesIO()
    Image.new("RGBA", (80,60), (20,40,60,0)).save(out, "PNG")
    return out.getvalue()


class ImportTests(unittest.TestCase):
    def test_legacy_equipment_adjacency_and_observed_arrow_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            document = {"components": [], "run": {"method": "test", "run_id": "r"},
                "connections": [{"source":"a", "target":"b", "kind":"direct_undirected",
                    "path":[[1,1],[2,2]], "direction":{"status":"source_to_target", "arrow_tip":[2,2]}}]}
            (Path(folder)/"prediction.json").write_text(json.dumps(document))
            _, edges, _ = analysis_objects(SimpleNamespace(directory=Path(folder),reference_citation=None,reference_url=None))
            self.assertEqual("direct_undirected", edges[0]["kind"])
            self.assertEqual("direct_undirected", edges[0]["observed_kind"])
            self.assertEqual("source_to_target", edges[0]["direction"])
            self.assertEqual([2,2], edges[0]["observed_arrow"]["arrow_tip"])
            document["run"]["method"] = "drawing-reference"
            (Path(folder)/"prediction.json").write_text(json.dumps(document))
            _, reviewed, _ = analysis_objects(SimpleNamespace(directory=Path(folder),reference_citation=None,reference_url=None))
            self.assertEqual("pipe", reviewed[0]["kind"])
            self.assertEqual("unreviewed", reviewed[0]["review_status"])

    def test_png_is_flattened_and_pdf_renders_to_image_first(self):
        frames = list(_image_frames("test.png", png()))
        self.assertEqual("RGB", frames[0][1].mode)
        self.assertEqual((255,255,255), frames[0][1].getpixel((0,0)))
        pdf = BytesIO()
        Image.new("RGB", (90,120), "white").save(pdf, "PDF", save_all=True,
            append_images=[Image.new("RGB", (90,120), "white")])
        pages = list(_image_frames("test.pdf", pdf.getvalue()))
        self.assertEqual([1,2], [p for p,_ in pages])
        self.assertEqual("RGB", pages[0][1].mode)
        self.assertEqual((2700,3600), pages[0][1].size)

    def test_unsafe_archive_path_rejected_and_index_read(self):
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("../escaped.png", png())
        with self.assertRaisesRegex(ValueError, "unsafe"):
            unpack([("drawings.zip", output.getvalue())])
        drawings, rows, errors = unpack([("test.png", png()), ("index.csv", b"file,drawing\ntest.png,T-1\n")])
        self.assertEqual(1, len(drawings))
        self.assertEqual("T-1", rows[0]["drawing"])
        self.assertEqual([], errors)

    def test_analysis_failure_retains_image_and_reports_partial_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            imports = Imports(store)
            try:
                job = store.job("test", "facility-local")
                with patch.object(imports, "analyze", side_effect=ValueError("test extractor failure")):
                    imports._run(job["id"], [("test.png", png())], "facility-local")
                state = store.snapshot()
                self.assertEqual("completed_with_errors", state["jobs"][0]["status"])
                sheet = state["sheets"][0]
                self.assertEqual("test extractor failure", sheet["source_metadata"]["analysis_error"])
                self.assertTrue((Path(folder)/"images"/(sheet["id"]+".png")).is_file())
                self.assertFalse(any("ready" in message for message in state["jobs"][0]["messages"]))
            finally:
                imports.executor.shutdown(wait=True)
                store.db.close()


if __name__ == "__main__":
    unittest.main()
