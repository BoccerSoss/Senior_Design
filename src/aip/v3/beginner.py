"""Install the six source PNGs into the workbench without running an extractor."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from ..io import ROOT


SOURCE = ROOT / "data/input/beginner"
COLLECTION = "ISLOCA beginner collection"


def _install_png(folder, sheet_id, record):
    data = (SOURCE / record["filename"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != record["png_sha256"]:
        raise ValueError(f"The bundled beginner drawing {record['filename']} failed its integrity check.")
    # Commit complete files first. Retrying an interrupted preload reuses the same sheet ID.
    output = NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False)
    try:
        with output:
            output.write(data)
        os.replace(output.name, folder / f"{sheet_id}.png")
    finally:
        Path(output.name).unlink(missing_ok=True)


def preload_beginner(store):
    source = json.loads((SOURCE / "source.json").read_text(encoding="utf-8"))
    images = store.directory / "images"
    images.mkdir(exist_ok=True)
    with store.lock:
        state = store.snapshot()
        facility = next((f for f in state["facilities"] if f["name"] == COLLECTION), None)
        if facility is None:
            facility = {"id": "facility-isloca-beginner", "name": COLLECTION}

            def add_collection(document):
                # A new workspace opens on the drawings; upgrading preserves its current drawing set.
                position = len(document["facilities"]) if document["sheets"] else 0
                document["facilities"].insert(position, facility)

            store.mutate(add_collection, "facility.created", details={"name": COLLECTION})
        facility_id = facility["id"]
        registered = {(sheet.get("source_sha256"), sheet.get("source_page")): sheet
                      for sheet in state["sheets"] if sheet["facility_id"] == facility_id}
        sheet_ids = []
        for record in source["pages"]:
            png_hash = record["png_sha256"]
            page = record["page"]
            # Earlier versions accepted individual page extracts as PDF or PNG, or an entire report.
            saved = (registered.get((png_hash, 1)) or registered.get((record["single_page_pdf_sha256"], 1))
                     or registered.get((source["report_sha256"], page)))
            if saved:
                if saved.get("source_sha256") == png_hash and not (images / f"{saved['id']}.png").is_file():
                    _install_png(images, saved["id"], record)
                sheet_ids.append(saved["id"])
                continue
            sheet_id = f"sheet-isloca-beginner-{page}"
            _install_png(images, sheet_id, record)
            figure = record["figure"]
            name = record["filename"]
            sheet = {"id": sheet_id, "facility_id": facility_id, "filename": name,
                     "title": f"{source['report']} · {figure}", "drawing_number": f"{figure} · p. {page}",
                     "sheet_number": str(page), "revision": "", "source_sha256": png_hash, "source_page": 1,
                     "width": record["width"], "height": record["height"], "image_url": f"/api/images/{sheet_id}",
                     "grid": {"columns": 12, "rows": 16, "origin": "top-left"}, "components": [], "connections": [],
                     "source_metadata": {"imported_filename": name, "source_document": source["report"],
                                         "source_url": source["source_url"], "figure": figure,
                                         "source_pdf_page": page, "pdf_converted_first": True}}
            sheet_ids.append(store.add_sheet(sheet)["sheet"]["id"])
        return {"facility_id": facility_id, "sheet_ids": sheet_ids}
