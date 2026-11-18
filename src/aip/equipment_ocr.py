"""Read equipment labels from native OCR regions, including labels split into words."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from statistics import median

from PIL import Image, ImageChops

from .baseline import connected_ink_spans
from .glyph_ocr import rank_tag_glyphs
from .windows_ocr import (batch_read_label_rows, _choose_box_text, _prefix_and_suffix,
                          _scanned_tag, _separator_suffix_box, recognize_image, recognize_label_boxes)


def _regions(reading):
    """Join nearby words on the same printed line, preserving the original pixels."""
    rows = []
    for word in sorted((w for w in reading["words"] if w["bbox"]), key=lambda w: w["bbox"][1] + w["bbox"][3]):
        _, top, _, bottom = word["bbox"]
        mid, height = (top + bottom) / 2, bottom - top
        if not rows or mid - rows[-1][0] > 0.4 * min(height, rows[-1][1]):
            rows.append((mid, height, []))
        rows[-1][2].append(word)
    for _, _, row in rows:
        words = sorted(row, key=lambda w: w["bbox"][0])
        i = 0
        while i < len(words):
            chunks = []
            for size in range(1, min(6, len(words) - i) + 1):
                part = words[i:i + size]
                box = part[-1]["bbox"]
                if size > 1 and box[0] - part[-2]["bbox"][2] > 1.4 * (box[3] - box[1]):
                    break
                text = " ".join(w["text"] for w in part)
                # A damaged separator can nominate a crop. Only actual glyph
                # evidence or repeated, valid text can supply its final label.
                compact = "".join(text.split())
                damaged = re.fullmatch(r"[A-Za-z0-9]{1,6}[-·•.‐‑–—]{1,6}[A-Za-z0-9·•.%-]{2,10}", compact)
                tag_word = re.fullmatch(r"[A-Za-z]{1,6}[-/][A-Za-z0-9]{1,10}", compact)
                if size > 1 and "-" in compact and not any(w["text"].endswith("-") or w["text"].startswith("-") for w in part):
                    damaged = tag_word = False
                    if not any(w["text"].endswith(("·", "•", "–", "—")) for w in part):
                        continue
                if _scanned_tag(text) or damaged or tag_word:
                    chunks.append((size, text, [min(w["bbox"][0] for w in part), min(w["bbox"][1] for w in part),
                                                max(w["bbox"][2] for w in part), max(w["bbox"][3] for w in part)]))
            if chunks:
                size, text, box = chunks[-1]
                yield text, box
                i += size
            else:
                i += 1


def recognize_equipment_labels(image_path, scratch_dir):
    """Retain source regions, complete label readings, and independently read suffixes."""
    scratch = Path(scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    if any(scratch.iterdir()):
        raise ValueError("Use an empty OCR scratch directory to preserve previous evidence.")
    native = recognize_image(image_path)
    records, crops = [], []
    with Image.open(image_path) as source:
        image = source.convert("L")
    for text, box in _regions(native):
        x0, y0, x1, y1 = box
        bounds = (max(0, math.floor(x0) - 3), max(0, math.floor(y0) - 3),
                  min(image.width, math.ceil(x1) + 3), min(image.height, math.ceil(y1) + 3))
        crop = image.crop(bounds)
        prefix, suffix = _prefix_and_suffix(crop)
        index = len(records)
        crops.append((index, False, crop))
        if prefix:
            crops.append((index, True, suffix))
        elif re.fullmatch(r"[A-Za-z]{1,6}-[A-Za-z0-9]{1,10}", text):
            suffix_box = _separator_suffix_box(image, box)
            if suffix_box:
                crops.append((index, True, image.crop(suffix_box)))
        records.append({"original_text": text, "source_word_bbox": box, "prefix_glyphs": prefix,
                        "read_bounds": bounds, "passes": [{"text": text, "suffix": False, "source": "full page"}]})
    for height in (32, 50):
        batch_read_label_rows(crops, height, scratch, records)
    # Give multiword names one isolated reading when the page and contact
    # sheets disagree. The numeric glyph matcher cannot resolve prose labels.
    descriptions = []
    for index, suffix, crop in crops:
        record = records[index]
        if (not suffix and " " in record["original_text"] and _scanned_tag(record["original_text"])
                and not record["prefix_glyphs"] and not _choose_box_text(record["passes"], None)[0]):
            descriptions.append((index, False, crop))
    batch_read_label_rows(descriptions, 42, scratch, records)
    for record in records:
        label, reason = _choose_box_text(record["passes"], record["prefix_glyphs"])
        if not record["prefix_glyphs"]:
            # When the prefix template is inconclusive, retain the actual OCR
            # prefix only if repeated full crops agree; confirm the suffix on its own.
            full = [p["text"].replace(" ", "").partition("-") for p in record["passes"] if not p["suffix"]]
            prefixes = [p for p, dash, _ in full if dash and re.fullmatch(r"[A-Za-z]{1,6}", p)]
            suffixes = [p["text"] for p in record["passes"] if p["suffix"]]
            if (len(prefixes) >= 2 and len({p.upper() for p in prefixes}) == 1 and len(suffixes) >= 2
                    and len(set(suffixes)) == 1 and re.fullmatch(r"[A-Za-z0-9]{1,10}", suffixes[0])):
                label, reason = prefixes[0] + "-" + suffixes[0], "repeated full-label prefix and separated suffix readings"
        record.update(selected_text=label, selection=reason)
    (scratch / "native.ocr.json").write_text(json.dumps(native, ensure_ascii=True), encoding="utf-8")
    return {"native": native, "tag_crop_passes": records, "crop_artifacts_retained": True,
            "method": "native OCR, word grouping, batched complete label and suffix crops; local glyph comparison"}


def read_damaged_tags(image, records):
    """Compare touching character shapes only for shortlisted, unread equipment labels."""
    numeric = [r for r in records if " " not in r["original_text"].strip() and
               any(c in r["original_text"] for c in "-·•.‐‑–—")]
    if not numeric:
        return
    gray = image.convert("L")
    for record in numeric[:8]:
        readings = rank_tag_glyphs(gray.crop(record["read_bounds"]))
        record["glyph_alternatives"] = readings
        if len(readings) >= 2 and readings[0]["mismatch"] <= 0.235 and readings[1]["mismatch"] - readings[0]["mismatch"] >= 0.004:
            record.update(selected_text=readings[0]["text"], selection="segmented glyph comparison on the associated label crop")


def ink_label_regions(image, words, *, remove_linework=False):
    """Find small clusters of adjacent printed glyphs, including words the page OCR missed."""
    heights = [w["bbox"][3] - w["bbox"][1] for w in words if w.get("bbox")]
    if not heights:
        return []
    height = median(heights)
    gray = image.convert("L")
    scale = 2 if gray.width > 1500 else 1
    reduced = gray.resize((max(1, gray.width // scale), max(1, gray.height // scale)), Image.Resampling.BOX)
    cutoff = 195 if remove_linework else 210
    pixels = reduced.point(lambda value: 255 if value < cutoff else 0)
    if remove_linework:
        data = bytearray(pixels.width * pixels.height)
        for spans, (left, top, right, bottom) in connected_ink_spans(pixels.tobytes(), *pixels.size):
            if 0.08 * height <= (bottom - top) * scale <= 1.6 * height and (right - left) * scale < 10 * height:
                for y, x, end in spans:
                    start = y * pixels.width
                    data[start + x:start + end] = bytes([255]) * (end - x)
        pixels = Image.frombytes("L", reduced.size, bytes(data))
    joined = pixels
    gap = max(1, round((0.65 if remove_linework else 0.33) * height / scale))
    row_gap = max(1, round(0.09 * height / scale)) if remove_linework else max(1, gap // 5)
    for horizontal, distance in ((True, gap), (False, row_gap)):
        added = 0
        while added < distance:
            step = min(added + 1, distance - added)
            shifted = Image.new("L", joined.size)
            shifted.paste(joined, (step, 0) if horizontal else (0, step))
            joined = ImageChops.lighter(joined, shifted)
            added += step
    regions = []
    for _, reduced_box in connected_ink_spans(joined.tobytes(), *joined.size):
        x, y, right, bottom = [value * scale for value in reduced_box]
        if not (1.4 * height <= right - x <= 16 * height and 0.32 * height <= bottom - y <= 2.2 * height):
            continue
        # The joining operation is only for locating a region. Keep the original ink for transcription.
        if remove_linework:
            bounds = pixels.crop(reduced_box).getbbox()
            if bounds and bounds[2] - bounds[0] > 1.8 * (bounds[3] - bounds[1]):
                regions.append({"source_word_bbox": [x + scale * bounds[0], y + scale * bounds[1],
                                                     min(gray.width, x + scale * bounds[2]),
                                                     min(gray.height, y + scale * bounds[3])]})
            continue
        box = gray.crop((x, y, right, bottom)).point(lambda value: 255 if value < 200 else 0).getbbox()
        if box and box[2] - box[0] > 2.6 * (box[3] - box[1]):
            regions.append({"source_word_bbox": [x + box[0], y + box[1], x + box[2], y + box[3]]})
    return regions


def _same_region(a, b):
    if not a or not b:
        return False
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return overlap > 0.55 * min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))


def fill_missing_regions(primary, alternate):
    """Use a second text mask only for unused words that the first mask missed."""
    assigned = [r["source_word_bbox"] for r in primary if r]
    return [first or (other if other and not any(_same_region(other["source_word_bbox"], used)
                                                for used in assigned) else None)
            for first, other in zip(primary, alternate)]


def read_ink_labels(image, nearby, associations, scratch_dir):
    """Reread automatically localized labels together, using the original image at several scales."""
    owners = [(a["component_id"], a["source_word_bbox"]) for a in associations if a["observed_tag"]]
    labels = []
    by_id = {a["component_id"]: a for a in associations}
    for region, prior in zip(nearby, associations):
        if not region:
            continue
        component = prior["component_id"]
        box = region["source_word_bbox"]
        if any(other != component and _same_region(box, used) for other, used in owners):
            continue
        previous = prior["source_word_bbox"]
        if previous and _same_region(box, previous):
            # The ink cluster may contain only a suffix, or the native word may
            # omit the printed prefix. Reread the complete overlapping word.
            box = [min(box[0], previous[0]), min(box[1], previous[1]),
                   max(box[2], previous[2]), max(box[3], previous[3])]
        elif previous and prior["observed_tag"]:
            continue
        labels.append({"id": component, "tag_bbox": box})
    if not labels:
        return []
    records = recognize_label_boxes(image, labels, scratch_dir=Path(scratch_dir) / "ink-rows")
    for record in records:
        if record["observed_tag"]:
            target = by_id[record["component_id"]]
            target.update(observed_tag=record["observed_tag"], source_word_bbox=record["source_word_bbox"],
                          native_text=record["native_text"], ocr_selection=record["ocr_selection"], uncertainty=None)
    return records
