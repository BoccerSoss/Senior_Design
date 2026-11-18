"""Convert drawing uploads to images before running the existing extractor."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from PIL import Image, ImageOps

from ..desktop_analysis import analyze_drawing
from ..io import load_json
from .store import ident

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
MAX_UPLOAD = 150 * 1024 * 1024
MAX_EXPANDED = 600 * 1024 * 1024


def index_rows(data):
    text = data.decode("utf-8-sig")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        rows.append({str(key).strip().lower(): str(value or "").strip() for key, value in row.items()
                     if key is not None})
    return rows


def analysis_objects(result):
    document = load_json(result.directory / "prediction.json")
    components = []
    for item in document["components"]:
        components.append({
            "id": item["id"], "tag": item["observed_tag"] or "", "type": item["type"],
            "bbox": item["bbox"], "tag_bbox": item.get("tag_bbox"),
            "normal_position": "unknown", "isolation_credit": "unverified", "review_status": "unreviewed",
            "evidence": item.get("evidence"), "uncertainty": item.get("uncertainty"),
        })
    connections = []
    for i, item in enumerate(document["connections"], 1):
        observed_direction = item.get("direction") or {}
        direction = observed_direction.get("status", "unknown") if isinstance(observed_direction, dict) else observed_direction
        observed_kind = item.get("kind", "unknown")
        # Preserve the original equipment-adjacency relation without claiming a
        # pipe/signal classification the generic extractor did not establish.
        kind = observed_kind if observed_kind in {"pipe", "signal", "direct_undirected"} else "unknown"
        if kind == "direct_undirected" and document["run"]["method"] == "drawing-reference":
            kind = "pipe"
        connections.append({"id": f"edge-{i:04d}", "source": item["source"], "target": item["target"],
                            "kind": kind, "observed_kind": observed_kind, "path": item.get("path"), "direction": direction,
                            "observed_arrow": observed_direction if isinstance(observed_direction, dict) else None,
                            "review_status": "unreviewed", "evidence": item.get("evidence"),
                            "uncertainty": item.get("uncertainty") or (
                                "Equipment adjacency under the simplified drawing convention; line classification is unconfirmed."
                                if kind == "direct_undirected" else "Review the connection against the source drawing.")})
    provenance = {"method": document["run"]["method"], "run_id": document["run"]["run_id"],
                  "citation": result.reference_citation, "source_url": result.reference_url,
                  "uncertainties": document.get("uncertainties", []),
                  "arrows": document.get("arrows", []), "flow_routes": document.get("flow_routes", [])}
    return components, connections, provenance


def _image_frames(name, data):
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise ValueError("PDF import needs the V2 dependencies. Run Open AIP V2.cmd.") from exc
        document = pdfium.PdfDocument(data)
        try:
            for index in range(len(document)):
                page = document[index]
                scale = 3600 / max(page.get_size())
                bitmap = page.render(scale=scale)
                try:
                    yield index + 1, bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
                    page.close()
        finally:
            document.close()
    elif suffix in IMAGE_EXTENSIONS:
        with Image.open(io.BytesIO(data)) as source:
            for frame in range(getattr(source, "n_frames", 1)):
                source.seek(frame)
                image = ImageOps.exif_transpose(source)
                if image.width * image.height > 30_000_000:
                    raise ValueError("A drawing image exceeds the 30 million pixel limit.")
                if "A" in image.getbands() or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    white = Image.new("RGBA", rgba.size, "white")
                    image = Image.alpha_composite(white, rgba)
                image = image.convert("RGB")
                image.info.clear()
                yield frame + 1, image
    elif suffix == ".dgn":
        converter = os.getenv("AIP_DGN_CONVERTER", "")
        executable = shutil.which(converter) if converter else None
        if not executable:
            raise ValueError("DGN needs a configured converter. Set AIP_DGN_CONVERTER to an executable "
                             "accepting input.dgn output.pdf, or export the sheet to PDF in your CAD tool.")
        with tempfile.TemporaryDirectory(prefix="aip-dgn-") as temp:
            input_file, output_file = Path(temp) / "input.dgn", Path(temp) / "output.pdf"
            input_file.write_bytes(data)
            process = subprocess.run([executable, str(input_file), str(output_file)], capture_output=True,
                                     timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if process.returncode or not output_file.is_file():
                raise ValueError("The configured DGN converter did not produce a PDF.")
            yield from _image_frames(output_file.name, output_file.read_bytes())
    else:
        raise ValueError(f"Unsupported drawing format {suffix or '(no extension)'}.")


def unpack(files):
    drawings, rows, errors = [], [], []
    total = 0
    for name, data in files:
        if len(data) > MAX_UPLOAD:
            raise ValueError("One uploaded file exceeds 150 MB.")
        if Path(name).suffix.lower() == ".csv":
            rows.extend(index_rows(data))
        elif Path(name).suffix.lower() == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for entry in archive.infolist():
                    if entry.is_dir():
                        continue
                    path = Path(entry.filename.replace("\\", "/"))
                    if path.is_absolute() or ".." in path.parts or ":" in entry.filename:
                        raise ValueError("Archive contains an unsafe member path.")
                    total += entry.file_size
                    if total > MAX_EXPANDED or entry.file_size > MAX_UPLOAD:
                        raise ValueError("Archive expands past the import limit.")
                    suffix = path.suffix.lower()
                    if suffix in IMAGE_EXTENSIONS | {".pdf", ".dgn", ".csv"}:
                        content = archive.read(entry)
                        if suffix == ".csv":
                            rows.extend(index_rows(content))
                        else:
                            drawings.append((path.name, content))
                    else:
                        errors.append(f"Skipped {path.name}. Unsupported file type.")
        else:
            drawings.append((Path(name).name, data))
    return drawings, rows, errors


def metadata_for(rows, filename, page):
    candidates = [row for row in rows if Path(row.get("file") or row.get("filename") or row.get("input_pdf")
                                             or row.get("png") or "").name == filename]
    exact = [row for row in candidates if row.get("page", str(page)) == str(page)]
    row = (exact or candidates or [{}])[0]
    return {"title": row.get("title") or row.get("figure_or_title") or Path(filename).stem,
            "drawing_number": row.get("drawing") or row.get("drawing_number") or Path(filename).stem,
            "sheet_number": row.get("sheet") or row.get("sheet_number") or str(page),
            "revision": row.get("revision", ""), "index": row}


class Imports:
    def __init__(self, store):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drawing-import")

    def submit(self, files, facility_id):
        if not any(f["id"] == facility_id for f in self.store.snapshot()["facilities"]):
            raise ValueError("Choose a facility for the imported drawings.")
        job = self.store.job("Import drawings", facility_id)
        self.executor.submit(self._run, job["id"], files, facility_id)
        return job

    def _run(self, job_id, files, facility_id):
        ids, messages, failures = [], [], 0
        try:
            self.store.update_job(job_id, status="running")
            drawings, index, messages = unpack(files)
            if not drawings:
                raise ValueError("No drawing files were found in the upload.")
            for file_index, (name, data) in enumerate(drawings):
                try:
                    for page, image in _image_frames(name, data):
                        metadata = metadata_for(index, name, page)
                        sheet_id = ident("sheet")
                        images = self.store.directory / "images"
                        images.mkdir(exist_ok=True)
                        image_file = images / f"{sheet_id}.png"
                        image.save(image_file, compress_level=9)
                        sheet = {
                            "id": sheet_id, "facility_id": facility_id, "filename": name,
                            "title": metadata["title"], "drawing_number": metadata["drawing_number"],
                            "sheet_number": metadata["sheet_number"], "revision": metadata["revision"],
                            "width": image.width, "height": image.height, "image_url": f"/api/images/{sheet_id}",
                            "source_sha256": hashlib.sha256(data).hexdigest(), "source_page": page,
                            "grid": {"columns": 12, "rows": 16, "origin": "top-left"},
                            "components": [], "connections": [],
                            "source_metadata": {"imported_filename": name, "pdf_converted_first": name.lower().endswith(".pdf"),
                                                "index": metadata["index"]},
                        }
                        inserted = self.store.add_sheet(sheet)
                        actual = inserted["sheet"]
                        ids.append(actual["id"])
                        if not inserted["duplicate"]:
                            try:
                                self.analyze(actual["id"])
                                messages.append(f"{name}, page {page}: converted and read; review observations")
                            except (ValueError, OSError, RuntimeError) as exc:
                                failures += 1
                                self.store.update_sheet(actual["id"], {"source_metadata": {
                                    **actual["source_metadata"], "analysis_error": str(exc)}})
                                messages.append(f"{name}, page {page}: image imported; analysis failed: {exc}")
                        else:
                            image_file.unlink(missing_ok=True)
                            messages.append(f"{name}, page {page}: already imported")
                        self.store.update_job(job_id, messages=messages[-100:], sheet_ids=ids,
                                              progress=round((file_index + 1) / len(drawings) * 100))
                except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    failures += 1
                    messages.append(f"{name}: {exc}")
            self.store.update_job(job_id, status=("completed_with_errors" if failures else "completed") if ids else "failed",
                                  progress=100, sheet_ids=ids, messages=messages,
                                  error=None if ids else "All drawing imports failed.")
        except (ValueError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
            self.store.update_job(job_id, status="failed", messages=messages, error=str(exc), sheet_ids=ids)

    def analyze(self, sheet_id, *, require_reference=False):
        image = self.store.directory / "images" / f"{sheet_id}.png"
        settings = self.store.snapshot()["settings"]
        result = analyze_drawing(image, self.store.directory / "analysis",
                                 use_reference=require_reference or settings.get("use_geometry_reference", True),
                                 require_reference=require_reference)
        components, connections, provenance = analysis_objects(result)
        outcome = self.store.replace_analysis(sheet_id, components, connections, provenance)
        sheet = outcome["sheet"]
        if "analysis_error" in sheet["source_metadata"]:
            metadata = dict(sheet["source_metadata"])
            metadata.pop("analysis_error")
            self.store.update_sheet(sheet_id, {"source_metadata": metadata})
        return outcome

    def reanalyze(self, sheet_id, *, require_reference=False):
        sheet = self.store.sheet(sheet_id)
        job = self.store.job(f"Read {sheet['title']}", sheet["facility_id"])
        def work():
            self.store.update_job(job["id"], status="running")
            try:
                result = self.analyze(sheet_id, require_reference=require_reference)
                self.store.update_job(job["id"], status="completed", progress=100,
                                      messages=[f"Kept {result['corrections_retained']} corrections."], sheet_ids=[sheet_id])
            except (ValueError, OSError, RuntimeError) as exc:
                self.store.update_job(job["id"], status="failed", error=str(exc))
        self.executor.submit(work)
        return job
