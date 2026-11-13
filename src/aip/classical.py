"""Automatic raster interpretation for a limited educational symbol vocabulary."""
from __future__ import annotations
from collections import Counter
import json
import re
from pathlib import Path
from PIL import Image
from .baseline import extract_image as geometry_extract
from .classification import classify_symbol
from .core import normalize_tag
from .windows_ocr import recognize_tag_crops

TAG_PATTERN = re.compile(r"[A-Za-z]{1,6}[-/][A-Za-z0-9]{1,10}")


def classical_info():
    return {"method": "windows-ocr-and-outline-rules-v1", "provider": "local Windows/Pillow",
            "model": None, "access_route": "local raster pixels, Windows.Media.Ocr and deterministic shape rules",
            "tag_method": "native OCR plus separator-derived suffix pixel crops at 1x/3x; raw readings retained",
            "direction_method": "visible arrowheads and connected raster paths through symbol candidates",
            "supported_symbols": ["simplified tank", "opposed-triangle valve", "circle pump with entry/tangent strokes"],
            "limitations": ["Limited educational vocabulary, horizontal pipe contacts only.",
                           "OCR and geometric association can fail; no reference-driven correction.",
                           "Correlated crop agreement is not confidence or human verification."]}


def associate_tags(components, crop_records, image_height):
    """Associate visible tag words above symbols using only their observed boxes.

    Each text region can support at most one symbol. Overlapping/competing text
    positions remain unresolved rather than using known tag prefixes or graph.
    """
    scored = []
    for ci,component in enumerate(components):
        x0,y0,x1,y1 = component["bbox"]
        width = x1-x0
        for i, record in enumerate(crop_records):
            text, box = record.get("selected_text"), record.get("source_word_bbox")
            if not isinstance(text,str) or not TAG_PATTERN.fullmatch(text) or not box:
                continue
            tx0,ty0,tx1,ty1 = box
            cx = (tx0+tx1)/2
            gap = y0-ty1
            if x0-0.1*width <= cx <= x1+0.1*width and 0 <= gap <= 0.35*image_height:
                score = gap/image_height + abs(cx-(x0+x1)/2)/max(width,1)
                scored.append((score,ci,i,record))
    associations = []
    for ci,component in enumerate(components):
        candidates = sorted((s for s in scored if s[1]==ci),key=lambda item:item[0])
        chosen = None
        if candidates and (len(candidates)==1 or candidates[1][0]-candidates[0][0] > 0.03):
            best = candidates[0]
            competing = sorted((s for s in scored if s[2]==best[2]),key=lambda item:item[0])
            if competing[0][1]==ci and (len(competing)==1 or competing[1][0]-competing[0][0]>0.03):
                chosen = best[3]
        associations.append({"component_id":component["id"],"observed_tag":chosen["selected_text"] if chosen else None,
            "source_word_bbox":chosen["source_word_bbox"] if chosen else None,
            "native_text":chosen["original_text"] if chosen else None,
            "ocr_selection":chosen["selection"] if chosen else None,
            "uncertainty":None if chosen else "No unambiguous tag-like OCR region above this symbol; tag remains unresolved."})
    return associations


def extract_image(image_path, scratch_dir):
    """Consume the image, then combine geometric, text and outline observations."""
    raw = geometry_extract(image_path)
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    if raw["components"]:
        ocr = recognize_tag_crops(image_path, scratch_dir=scratch_dir)
        if ocr["native"]["image"] != raw["image"]:
            raise ValueError("OCR image dimensions disagree with the geometry image; no observations accepted.")
        associations = associate_tags(raw["components"],ocr["tag_crop_passes"],image.height)
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
