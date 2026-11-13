"""Use a source-checked drawing record only when its prepared image matches."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import shutil

from .core import ValidationError, normalize_response, normalize_tag
from .exporting import export_document
from .io import ROOT, load_json, new_run, read_packet, write_json
from .label_review import read_label_review
from .windows_ocr import recognize_label_boxes

REFERENCE_DIR = ROOT / "data" / "drawing_references"
REFERENCE_METHOD = "drawing-reference"


def matching_reference(drawing, library=REFERENCE_DIR):
    if not library.is_dir():
        return None
    for path in sorted(library.glob("*.json")):
        record = load_json(path)
        image = record["image"]
        source_sha = image.get("source_sha256")
        if image["sha256"] == drawing["sha256"] or (source_sha and source_sha == drawing.get("source_sha256")):
            if (image["width"], image["height"]) != (drawing["width"], drawing["height"]):
                raise ValidationError(f"Drawing reference {path.name} has inconsistent image dimensions.")
            return path, record
    return None


def export_reference(packet, runs_dir, match, label_response=None):
    """Read labels from image pixels using source-checked label locations."""
    path, record = match
    metadata = read_packet(packet)
    citation = record["citation"]
    url = record["source_url"]
    directory, run = new_run(runs_dir, REFERENCE_METHOD, "local Windows OCR and drawing geometry",
                             packet=packet, stage="reviewed")
    run.update(
        prompt_version=metadata["prompt_version"],
        prompt_sha256=metadata["prompt_sha256"],
        raw_response_location="ocr_evidence.json",
        access_route="local OCR on exact-image-matched source-checked label boxes",
        settings={"citation": citation, "source_url": url, "reference_file": path.name},
    )
    write_json(directory / "input_manifest.json", metadata)
    try:
        write_json(directory / "source_geometry.json", record)
        raw = deepcopy(record["observations"])
        components = raw["components"]
        if any("observed_tag" in item for item in components):
            raise ValidationError("Drawing geometry may contain tag boxes only. Equipment names must come from OCR.")
        label_boxes = [{"id": item["id"], "tag_bbox": item["tag_bbox"]} for item in components
                       if item.get("tag_bbox") is not None]
        image = packet / "image.png"
        if label_response is None:
            ocr = recognize_label_boxes(image, label_boxes)
            write_json(directory / "ocr_evidence.json", ocr)
            by_id = {item["component_id"]: item["observed_tag"] for item in ocr}
        else:
            response = directory / "vision_response.txt"
            shutil.copyfile(label_response, response)
            run.update(provider="user-provided image transcription", raw_response_location=response.name,
                       access_route="label-crop vision response imported by user")
            by_id = read_label_review(response, [item["id"] for item in label_boxes], record["image"]["sha256"][:16])
        tags = [normalize_tag(by_id.get(item["id"])) for item in components]
        tag_counts = Counter(tags)
        for item, tag in zip(components, tags):
            if tag is not None and tag_counts[tag] > 1:
                item["observed_tag"] = None
                item["uncertainty"] = "OCR repeated this label at more than one symbol."
            else:
                item["observed_tag"] = by_id.get(item["id"])
                if tag is None and item.get("tag_bbox") is not None:
                    item["uncertainty"] = "No reading was returned for this printed label."
        run["status"] = "validated"
        document = normalize_response(raw, metadata["drawing"], run)
        unprinted = {f"components[{index}]" for index, item in enumerate(components) if item.get("tag_bbox") is None}
        document["warnings"] = [message for message in document["warnings"]
                                if message.partition(" ")[0] not in unprinted]
        for item in document["components"]:
            item["review_status"] = "source-checked geometry, image-transcribed label"
        for edge in document["connections"]:
            edge["review_status"] = "checked against source drawing"
        write_json(directory / "prediction.json", document)
        export_document(document, directory / "exports", image)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        run["status"] = "failed_reference_analysis"
        write_json(directory / "failure.json", {"status": "failed", "error": str(exc)})
        raise
    finally:
        write_json(directory / "run.json", run)
    return directory
