"""Read equipment labels from native OCR regions, including labels split into words."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from PIL import Image

from .glyph_ocr import rank_tag_glyphs
from .windows_ocr import _choose_box_text, _prefix_and_suffix, _scanned_tag, _separator_suffix_box, recognize_image


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


def _batch_read(crops, height, scratch, records):
    """Read multiple isolated source crops in one OCR call, then map words back to rows."""
    padding = 20
    row_height = height + padding
    for start in range(0, len(crops), 24):
        batch = crops[start:start + 24]
        widths = [max(1, min(1600, round(c.width * height / c.height))) for _, _, c in batch]
        canvas = Image.new("L", (max(widths) + 2 * padding, row_height * len(batch)), 255)
        for row, ((_, _, crop), width) in enumerate(zip(batch, widths)):
            canvas.paste(crop.resize((width, height), Image.Resampling.LANCZOS), (padding, row * row_height + padding // 2))
        filename = scratch / f"label-rows-{height}-{start}.png"
        canvas.save(filename)
        reading = recognize_image(filename)
        words_by_row = [[] for _ in batch]
        for word in reading["words"]:
            if word["bbox"] is None:
                continue
            left, top, _, bottom = word["bbox"]
            row = math.floor((top + bottom) / (2 * row_height))
            if 0 <= row < len(batch) and top >= row * row_height and bottom <= (row + 1) * row_height:
                words_by_row[row].append((left, word["text"]))
        for row, (index, suffix, _) in enumerate(batch):
            text = " ".join(word for _, word in sorted(words_by_row[row]))
            records[index]["passes"].append({"text": text, "suffix": suffix, "row": row,
                                             "crop_image_filename": filename.name})


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
        _batch_read(crops, height, scratch, records)
    # Give multiword names one isolated reading when the page and contact
    # sheets disagree. The numeric glyph matcher cannot resolve prose labels.
    descriptions = []
    for index, suffix, crop in crops:
        record = records[index]
        if (not suffix and " " in record["original_text"] and _scanned_tag(record["original_text"])
                and not record["prefix_glyphs"] and not _choose_box_text(record["passes"], None)[0]):
            descriptions.append((index, False, crop))
    _batch_read(descriptions, 42, scratch, records)
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
