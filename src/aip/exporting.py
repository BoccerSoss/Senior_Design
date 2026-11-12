"""Labeled portable exports and image-grounded review overlays."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from .core import ValidationError, validate_document
from .io import image_info
from .markings import build_markings


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def export_document(document: dict, outdir, image_path=None) -> dict:
    validate_document(document)
    root = Path(outdir)
    overlay_image = None
    has_marks = (any(c["bbox"] is not None or c.get("tag_bbox") is not None for c in document["components"])
                 or any(edge.get("path") is not None for edge in document["connections"]))
    if image_path is not None and has_marks:
        image_digest = hashlib.sha256(Path(image_path).read_bytes()).hexdigest()
        permitted_digests = {document["drawing"]["sha256"].lower()}
        if document["drawing"].get("source_sha256"):
            permitted_digests.add(document["drawing"]["source_sha256"].lower())
        if image_digest not in permitted_digests:
            raise ValidationError("overlay image SHA-256 differs from observation input; supply the recorded original/prepared image")
        # Match packet preparation exactly, including 180-degree EXIF transforms
        # that preserve dimensions and transparent pixels composited on white.
        overlay_image = image_info(image_path)
        if overlay_image.size != (document["drawing"]["width"], document["drawing"]["height"]):
            raise ValidationError("overlay input dimensions disagree with observation drawing; use the original image")
    if overlay_image is None and (root / "overlay.png").exists():
        raise ValidationError("output contains a previous overlay but this export has no supported overlay; use a fresh output directory to preserve prior evidence")
    root.mkdir(parents=True, exist_ok=True)
    by_id = {c["id"]: c for c in document["components"]}
    # Known tags first, then unresolved observations, so every identity remains visible.
    components = sorted(document["components"], key=lambda c: (c["normalized_tag"] is None, c["normalized_tag"] or c["id"]))
    labels = {c["id"]: c["normalized_tag"] if c["normalized_tag"] is not None else f"unresolved:{c['id']}" for c in components}
    # Observation IDs accompany all labels; unresolved labels never masquerade as tags.
    equipment = [{**c, "matrix_label": labels[c["id"]]} for c in components]
    if len(set(labels.values())) != len(labels):
        raise ValidationError("matrix labels collide with an unresolved placeholder; rename observation IDs before export")
    edges = [{**e, "source_tag": by_id[e["source"]]["normalized_tag"],
              "target_tag": by_id[e["target"]]["normalized_tag"]} for e in document["connections"]]
    markings = build_markings(document["drawing"], equipment, edges, labels, overlay_image)
    _write_json(root / "equipment.json", equipment)
    _write_json(root / "edges.json", edges)
    _write_json(root / "markings.json", markings)
    with (root / "equipment.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = ["id", "observed_tag", "normalized_tag", "type", "bbox", "evidence", "uncertainty", "review_status", "matrix_label"]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for c in equipment:
            row = {k: c.get(k) for k in fields}
            row["bbox"] = json.dumps(c["bbox"]) if c["bbox"] is not None else ""
            writer.writerow(row)
    with (root / "edges.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = ["source", "target", "source_tag", "target_tag", "kind", "evidence", "uncertainty", "review_status"]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows([{k: e.get(k) for k in fields} for e in edges])
    pairs = {frozenset((e["source"], e["target"])) for e in edges}
    with (root / "adjacency.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["component_id"] + [labels[c["id"]] for c in components])
        for c in components:
            writer.writerow([labels[c["id"]]] + [int(frozenset((c["id"], d["id"])) in pairs) for d in components])
    review = ["Observed direct undirected connections", ""]
    for edge, marking in zip(edges, markings["connections"]):
        direction = marking["direction"]
        flow = f"{labels[direction['from']]} -> {labels[direction['to']]}" if direction["from"] else "unknown"
        review.append(f"{labels[edge['source']]} -- {labels[edge['target']]} | flow: {flow}"
                      f" | evidence: {edge['evidence'] or 'not supplied'}"
                      f" | uncertainty: {edge['uncertainty'] or 'none reported'}")
    if not edges:
        review.append("No direct connections reported.")
    missing_boxes = [c["id"] for c in components if c["bbox"] is None]
    review.extend(["", "Missing box observations (not drawn): " + (", ".join(missing_boxes) or "none"),
                   "markings.json records observed coordinates and the exact positions drawn on the overlay.",
                   "Review status: pending human review."])
    (root / "review.txt").write_text("\n".join(review) + "\n", encoding="utf-8")
    outputs = {name: str(root / name) for name in ["equipment.json", "equipment.csv", "edges.json", "edges.csv",
                                                  "adjacency.csv", "markings.json", "review.txt"]}
    if overlay_image is not None:
        overlay_image.save(root / "overlay.png")
        outputs["overlay.png"] = str(root / "overlay.png")
    return outputs
