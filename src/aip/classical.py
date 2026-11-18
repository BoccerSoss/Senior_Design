"""Automatic raster interpretation for a limited educational symbol vocabulary."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
from PIL import Image
from .baseline import extract_image as geometry_extract
from .classification import classify_symbol
from .symbol_proposals import recover_symbol_boxes
from .core import normalize_tag
from .equipment_ocr import fill_missing_regions, ink_label_regions, read_damaged_tags, read_ink_labels, recognize_equipment_labels


def classical_info():
    return {"method": "windows-ocr-and-outline-rules-v2", "provider": "local Windows/Pillow",
            "model": None, "access_route": "local raster pixels, Windows.Media.Ocr and deterministic shape rules",
            "tag_method": "page and crop OCR with spatial word association, linework-filtered text regions, and cross-checked glyph readings",
            "direction_method": "visible arrowheads and connected raster paths through symbol candidates",
            "supported_symbols": ["simplified tank", "rounded vessel", "opposed-triangle, diagonal and angle valve bodies",
                                  "circular pump with entry/tangent strokes", "scroll pump housing"],
            "limitations": ["Limited educational vocabulary, horizontal pipe contacts only.",
                           "OCR and geometric association can fail; no reference-driven correction.",
                           "Correlated crop agreement is not confidence or human verification."]}


def _associate_regions(components, crop_records, image_height):
    """Associate adjacent, separately printed labels using only their observed boxes.

    Each text region can support at most one symbol. Overlapping/competing text
    positions remain unresolved rather than using known tag prefixes or graph.
    """
    scored = []
    for ci,component in enumerate(components):
        x0,y0,x1,y1 = component["bbox"]
        width, height = x1-x0, y1-y0
        if component.get("type") == "junction" or not width or not height or max(width, height) > 6 * min(width, height):
            continue
        # A merged linework region can enclose several unrelated labels. An
        # exterior label cannot safely identify that whole region as one symbol.
        if any(x0 < (b[0]+b[2])/2 < x1 and y0 < (b[1]+b[3])/2 < y1
                 for r in crop_records if (b := r.get("source_word_bbox"))):
            continue
        for i, record in enumerate(crop_records):
            box = record.get("source_word_bbox")
            if not box:
                continue
            tx0,ty0,tx1,ty1 = box
            line_height = max(1, ty1 - ty0)
            max_gap = min(0.25 * image_height, max(5.5 * line_height, 0.8 * min(width, height)))
            dx = abs(tx0 + tx1 - x0 - x1) / 2
            dy = abs(ty0 + ty1 - y0 - y1) / 2
            vertical_gap = max(ty0-y1, y0-ty1)
            side_gap = max(tx0-x1, x0-tx1)
            if 0 <= vertical_gap <= max_gap and dx <= max(0.60 * width, line_height):
                if vertical_gap > 2.5 * line_height and dx > 0.20 * width:
                    continue
                score = vertical_gap / max_gap + dx / width
            elif 0 <= side_gap <= min(max_gap, (4.5 if dy < 0.4 * line_height else 2.5) * line_height) and dy <= min(1.3 * line_height, 0.55 * height):
                score = side_gap / max_gap + dy / height
            else:
                continue
            scored.append((score,ci,i,record))
    associations = []
    for ci,component in enumerate(components):
        candidates = sorted((s for s in scored if s[1]==ci),key=lambda item:item[0])
        chosen = None
        if candidates and (len(candidates)==1 or candidates[1][0]-candidates[0][0] > 0.12):
            best = candidates[0]
            competing = sorted((s for s in scored if s[2]==best[2]),key=lambda item:item[0])
            if competing[0][1]==ci and (len(competing)==1 or competing[1][0]-competing[0][0]>0.12):
                chosen = best[3]
        associations.append(chosen)
    return associations


def associate_tags(components, crop_records, image_height):
    valid = [r for r in crop_records if isinstance(r.get("selected_text"), str) and r["selected_text"].strip()]
    chosen = _associate_regions(components, valid, image_height)
    return [{"component_id":item["id"], "observed_tag":r["selected_text"] if r else None,
             "source_word_bbox":r["source_word_bbox"] if r else None,
             "native_text":r["original_text"] if r else None, "ocr_selection":r["selection"] if r else None,
             "uncertainty":None if r else "No unambiguous adjacent equipment label; tag remains unresolved."}
            for item, r in zip(components, chosen)]


def extract_image(image_path, scratch_dir):
    """Consume the image, then combine geometric, text and outline observations."""
    raw = geometry_extract(image_path, refine_boxes=recover_symbol_boxes)
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    if raw["components"]:
        ocr = recognize_equipment_labels(image_path, scratch_dir)
        if ocr["native"]["image"] != raw["image"]:
            raise ValueError("OCR image dimensions disagree with the geometry image; no observations accepted.")
        records = ocr["tag_crop_passes"]
        damaged = {tuple(r["source_word_bbox"]): r for r in records if not r.get("selected_text") and r.get("read_bounds")}
        if damaged:
            provisional = [{**r, "selected_text": r["selected_text"] or r["original_text"]} for r in records]
            nearby = associate_tags(raw["components"], provisional, image.height)
            chosen = [damaged[tuple(a["source_word_bbox"])] for a in nearby
                      if a["source_word_bbox"] and tuple(a["source_word_bbox"]) in damaged]
            read_damaged_tags(image, chosen)
        associations = associate_tags(raw["components"],records,image.height)
        if any(not a["observed_tag"] for a in associations):
            regions = ink_label_regions(image, ocr["native"].get("words", []))
            nearby = _associate_regions(raw["components"], regions, image.height)
            if any(not region and not a["observed_tag"] for region, a in zip(nearby, associations)):
                cleaned = ink_label_regions(image, ocr["native"].get("words", []), remove_linework=True)
                alternatives = _associate_regions(raw["components"], cleaned, image.height)
                nearby = fill_missing_regions(nearby, alternatives)
            ocr["ink_label_readings"] = read_ink_labels(image, nearby, associations, scratch_dir)
    else:
        ocr = {"method":"OCR not invoked because no supported geometric candidates were detected", "native":None,
               "tag_crop_passes":[],"crop_artifacts_retained":False}
        associations = []
    normalized_tags = [normalize_tag(association["observed_tag"]) for association in associations]
    tag_counts = Counter(normalized_tags)
    for component, association, normalized_tag in zip(raw["components"], associations, normalized_tags):
        classification = classify_symbol(image,component["bbox"])
        observed_tag = association["observed_tag"]
        duplicate = normalized_tag is not None and tag_counts[normalized_tag] > 1
        component["type"] = classification["type"]
        component["observed_tag"] = None if duplicate else observed_tag
        component["tag_bbox"] = association["source_word_bbox"]
        component["evidence"] += " " + classification["evidence"]
        if observed_tag is not None:
            component["evidence"] += " OCR association: " + json.dumps(association,ensure_ascii=True)
        notes = [x for x in [classification["uncertainty"],association["uncertainty"]] if x]
        if duplicate:
            notes.append(f"OCR tag {observed_tag!r} matches text at multiple candidates. Identity is unresolved.")
        notes.append("Automatic development observation; independent human reference review pending.")
        component["uncertainty"] = " ".join(notes)
    raw["uncertainties"] = [
        "The local method detects visible arrowheads and traces supported horizontal runs through symbol candidates.",
        "All OCR passes, original words and source crop boxes are preserved; crop agreement is correlated evidence.",
        "Branch/crossing interpretation, unfamiliar symbols and tag association need review; no operating state is inferred.",
    ]
    if not raw["components"]:
        raw["uncertainties"].append("No supported symbol-like region detected; no memorized equipment or graph supplied.")
    raw["method_evidence"] = {"settings":classical_info(),"ocr":ocr,"associations":associations}
    return raw
