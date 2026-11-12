"""Optional local Windows.Media.Ocr adapter; input raster pixels only.

No provider login, network access, source vector, reference, or expected labels
are used. The Windows OCR language capability is an OS prerequisite, not a pip
dependency. API words do not include confidence, so it is recorded as null.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import string
import sys
import time
import tempfile
from collections import Counter
from contextlib import nullcontext
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from threading import local

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .core import normalize_tag
from .glyph_ocr import rank_tag_glyphs


_apartment = local()


class OCRUnavailable(ValueError):
    """The optional Windows OCR engine could not produce usable observations."""


def _original_box(box, angle, width, height):
    """Clockwise around image center, as documented for OcrResult.TextAngle."""
    radians = math.radians(angle or 0)
    cosine, sine = math.cos(radians), math.sin(radians)
    cx, cy = width / 2, height / 2
    points = []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
        dx, dy = x-cx, y-cy
        points.append([cx+dx*cosine-dy*sine, cy+dx*sine+dy*cosine])
    bounds = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
    if not (0 <= bounds[0] < bounds[2] <= width and 0 <= bounds[1] < bounds[3] <= height):
        return None, points
    return bounds, points


def recognize_image(image_path, timeout=90, working_directory=None):
    """Return actual OCR words/boxes without tag correction or symbol inference.

    Decode only the prepared raster and pass its pixels to Windows OCR.
    Rotate engine boxes by TextAngle around the image center to return original
    image coordinates, following Microsoft's documented overlay.
    """
    if sys.platform != "win32":
        raise OCRUnavailable("Windows.Media.Ocr requires Windows 10/11 and an installed OCR language. Use the baseline or manual importer on this platform.")
    image = Path(image_path).resolve()
    if not image.is_file():
        raise OCRUnavailable(f"OCR input image does not exist: {image.name}")
    if image.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise OCRUnavailable("Windows OCR adapter accepts a prepared PNG/JPEG raster only.")
    if working_directory is not None and not Path(working_directory).is_dir():
        raise OCRUnavailable("Windows OCR working directory must be an existing private scratch directory.")

    started = time.perf_counter()
    try:
        with Image.open(image) as source:
            result = asyncio.run(_recognize_bitmap(source, timeout))
    except TimeoutError as exc:
        raise OCRUnavailable(f"Windows OCR timed out after {timeout} seconds.") from exc
    except ImportError as exc:
        raise OCRUnavailable("Windows OCR is unavailable because its runtime could not load. Run Open AIP Demo.cmd to install the project dependencies.") from exc
    except (OSError, Image.DecompressionBombError) as exc:
        raise OCRUnavailable(f"Windows OCR unavailable: {exc}") from exc
    result["duration_seconds"] = round(time.perf_counter() - started, 6)
    return result


async def _recognize_bitmap(source, timeout):
    # Import only for Windows OCR so the other extraction paths can run without it.
    from winrt.runtime import ApartmentType, init_apartment
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine

    # The projection caches WinRT factories. Keep this thread's apartment
    # alive across the full image and all crop passes in the worker process.
    if not getattr(_apartment, "initialized", False):
        init_apartment(ApartmentType.MULTI_THREADED)
        _apartment.initialized = True

    width, height = source.size
    maximum = OcrEngine.max_image_dimension
    if max(width, height) > maximum:
        raise OCRUnavailable(f"Image exceeds the Windows OCR limit of {maximum} pixels per side. Resize a copy of the image before running OCR.")

    warnings = ["Windows.Media.Ocr does not expose word confidence. Confidence values are null."]
    engine = OcrEngine.try_create_from_language(Language("en-US"))
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
        warnings.append("English OCR was unavailable. The installed user-profile OCR language was used.")
    if engine is None:
        raise OCRUnavailable("Windows OCR is unavailable because there is no installed OCR recognizer language. Install a Windows OCR language or use the manual importer.")

    pixels = source.convert("RGBA").tobytes("raw", "BGRA")
    with SoftwareBitmap.create_copy_with_alpha_from_buffer(
        pixels, BitmapPixelFormat.BGRA8, width, height, BitmapAlphaMode.IGNORE
    ) as bitmap:
        async with asyncio.timeout(timeout):
            reading = await engine.recognize_async(bitmap)

    angle = reading.text_angle
    if angle is not None and not math.isfinite(angle):
        raise OCRUnavailable("Windows OCR returned invalid TextAngle. The original coordinates could not be recovered.")
    words, lines = [], []
    for line in reading.lines:
        line_words = []
        for word in line.words:
            rect = word.bounding_rect
            engine_box = [rect.x, rect.y, rect.x + rect.width, rect.y + rect.height]
            if not (0 <= engine_box[0] < engine_box[2] <= width and 0 <= engine_box[1] < engine_box[3] <= height):
                raise OCRUnavailable("Windows OCR returned an unsupported or out-of-bounds word box.")
            box, polygon = _original_box(engine_box, angle, width, height)
            if box is None:
                warnings.append("An OCR box rotated outside the image. Its raw engine box and polygon are retained.")
            line_words.append({"text": word.text, "bbox": box, "confidence": None,
                               "engine_bbox": engine_box, "polygon": polygon})
        lines.append({"text": line.text, "words": line_words,
                      "bbox": _enclosing_box(line_words, "bbox"),
                      "engine_bbox": _enclosing_box(line_words, "engine_bbox")})
        words.extend(line_words)
    return {
        "engine": "Windows.Media.Ocr",
        "available": True,
        "image": {"width": width, "height": height},
        "language": engine.recognizer_language.language_tag,
        "available_languages": [language.language_tag for language in OcrEngine.available_recognizer_languages],
        "max_image_dimension": maximum,
        "windows_version": ".".join(str(part) for part in sys.getwindowsversion().platform_version),
        "winrt_runtime_version": version("winrt-runtime"),
        "text_angle_degrees": angle,
        "coordinate_convention": "Top-left original image pixels with half-open boxes after clockwise TextAngle rotation around the image center. Engine boxes and polygons are retained.",
        "words": words,
        "lines": lines,
        "warnings": warnings,
    }


def _enclosing_box(words, key):
    boxes = [word[key] for word in words if word[key] is not None]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _separator_suffix_box(image, box):
    """Find an isolated short horizontal separator from observed word pixels."""
    left, top, right, bottom = [int(v) for v in box]
    word = image.crop((left, top, right, bottom)).convert("L")
    width, height = word.size
    ink = {(x, y) for y in range(height) for x in range(width) if word.getpixel((x,y)) < 160}
    candidates = []
    while ink:
        point = ink.pop(); stack = [point]; group = [point]
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if (nx,ny) in ink:
                    ink.remove((nx,ny)); stack.append((nx,ny)); group.append((nx,ny))
        x0, x1 = min(p[0] for p in group), max(p[0] for p in group)+1
        y0, y1 = min(p[1] for p in group), max(p[1] for p in group)+1
        if x1-x0 >= max(3,1.4*(y1-y0)) and y1-y0 < 0.45*height and 0.1*width < x0 < 0.7*width and 0.2*height < (y0+y1)/2 < 0.85*height:
            candidates.append((x0,x1))
    if len(candidates) != 1:
        return None
    _, separator_end = candidates[0]
    # Whitespace starts just after the detected separator; include vertical and
    # right margin without synthesizing any glyphs or forcing a digit alphabet.
    return [left+separator_end+1, max(0,top-12), min(image.width,right+12), min(image.height,bottom+12)]


_NUMBERED_TAG = re.compile(r"[A-Za-z][A-Za-z0-9]{0,5}[-/](?=[A-Za-z0-9]*\d)[A-Za-z0-9]{1,10}(?:/[A-Za-z0-9]{1,4})?")
_EQUIPMENT_NAME = re.compile(
    r"(?:(?:[A-Z0-9']{2,10}) )?(?:Pump|Valve|Tank|Vessel|Compressor|Turbine|Exchanger|Strainer) [A-Z0-9]{1,4}(?:/[A-Z0-9]{1,4})?",
    re.IGNORECASE,
)
_EQUIPMENT_DESCRIPTION = re.compile(
    r"(?:[A-Za-z]+ ){1,4}(?:Tank|Vessel|Compressor|Turbine|Exchanger|Strainer)", re.IGNORECASE,
)
_DASHES = str.maketrans({char: "-" for char in "‐‑‒–—−﹘﹣－"})


def _scanned_tag(text):
    compact = re.sub(r"\s+", "", text.translate(_DASHES))
    if _NUMBERED_TAG.fullmatch(compact):
        return compact
    spaced = " ".join(text.split())
    if _EQUIPMENT_NAME.fullmatch(spaced) or _EQUIPMENT_DESCRIPTION.fullmatch(spaced):
        return spaced
    return None


def _ink_columns(image):
    """Return contiguous vertical ink bands and their actual bounds."""
    ink = image.point(lambda value: 255 if value < 160 else 0)
    occupied = ink.resize((ink.width, 1), Image.Resampling.BOX)
    runs = re.finditer(rb"[^\x00]+", occupied.tobytes())
    bands = []
    for run in runs:
        left, right = run.span()
        glyph = ink.crop((left, 0, right, ink.height))
        bounds = glyph.getbbox()
        if bounds is not None:
            bands.append((left, right, glyph.crop(bounds)))
    return bands


def _glyph_signature(glyph):
    return glyph.width / glyph.height, glyph.resize((16, 24), Image.Resampling.LANCZOS).tobytes()


@lru_cache(maxsize=1)
def _letter_shapes():
    """Build single-character comparison shapes independent of the input."""
    font = ImageFont.load_default(size=36)
    templates = []
    for char in string.ascii_uppercase:
        image = Image.new("L", (64, 64))
        ImageDraw.Draw(image).text((4, 4), char, font=font, fill=255)
        templates.append((char, *_glyph_signature(image.crop(image.getbbox()))))
    return templates


def _read_letter(glyph):
    aspect, pixels = _glyph_signature(glyph)
    candidates = sorted(
        (sum(abs(a - b) for a, b in zip(pixels, template)) / (255 * len(pixels)) + 0.22 * abs(aspect - ratio), char)
        for char, ratio, template in _letter_shapes()
    )
    first, second = candidates[:2]
    return first[1] if first[0] <= 0.36 and second[0] - first[0] >= 0.045 else None


def _prefix_and_suffix(crop):
    # A short centered horizontal dash can split a tag into independently
    # readable parts. Require a clear separator and individually separated
    # prefix letters before using a per-glyph comparison.
    ink_bounds = crop.point(lambda value: 255 if value < 160 else 0).getbbox()
    if ink_bounds is None:
        return None, None
    crop = crop.crop(ink_bounds)
    bands = _ink_columns(crop)
    for index, (left, right, glyph) in enumerate(bands[1:6], 1):
        if index + 1 == len(bands) or glyph.width < 1.8 * glyph.height or glyph.height > 0.36 * crop.height:
            continue
        prefix = [_read_letter(item[2]) for item in bands[:index] if item[2].height >= 0.55 * crop.height]
        if len(prefix) == index and all(prefix):
            return "".join(prefix), crop.crop((right, 0, crop.width, crop.height))
    return None, None


def _text_pass(crop, scale, padding, name, temp, timeout):
    view = ImageOps.expand(crop, padding, fill=255)
    view = view.resize((round(view.width * scale[0]), round(view.height * scale[1])), Image.Resampling.LANCZOS)
    path = Path(temp) / (name + ".png")
    view.save(path)
    reading = recognize_image(path, timeout, working_directory=temp)
    return {"text": " ".join(word["text"] for word in reading["words"]), "scale": scale, "padding": padding}


def _choose_box_text(passes, prefix):
    if prefix:
        # Compare independent suffix crops with full-box text. Repeated ink
        # evidence can supply a digit even when the recognizer merges glyphs
        # on the other side of the dash. Preserve the original readings.
        evidence = []
        for entry in passes:
            compact = re.sub(r"[^A-Za-z0-9/\-]", "", entry["text"].translate(_DASHES)).upper()
            suffix = re.split(r"[-/]", compact)[-1]
            if re.fullmatch(r"\d{2,7}[A-Z]?", suffix):
                evidence.append(suffix)
        if evidence:
            counts = Counter(evidence)
            leading, votes = counts.most_common(1)[0]
            if votes >= 2 and list(counts.values()).count(votes) == 1:
                return f"{prefix}-{leading}", "glyph comparison for prefix and repeated OCR for numeric suffix"
    values = [_scanned_tag(entry["text"]) for entry in passes if not entry.get("suffix")]
    labels = [value for value in values if value]
    if labels:
        counts = Counter(normalize_tag(label) for label in labels)
        leading, votes = counts.most_common(1)[0]
        if votes >= 2 and list(counts.values()).count(votes) == 1:
            return next(label for label in labels if normalize_tag(label) == leading), "repeated OCR of the complete label crop"
    return None, None


def _observed_suffixes(passes):
    texts = [re.sub(r"\s+", "", entry["text"].translate(_DASHES)).upper() for entry in passes]
    suffixes = [text.split("-", 1)[1] for text in texts if "-" in text]
    return {suffix for suffix, count in Counter(suffixes).items()
            if count >= 2 and re.fullmatch(r"\d{2,7}[A-Z]?(?:/[A-Z])?", suffix)}


def _page_prefix(results):
    """Find a prefix repeated in several independently read labels."""
    prefixes = []
    for record in results:
        observed = record["observed_tag"]
        if not observed or not _NUMBERED_TAG.fullmatch(observed):
            continue
        prefix = observed.upper().partition("-")[0]
        if prefix.isalpha() and len(prefix) >= 2:
            prefixes.append(prefix)
    if not prefixes:
        return None
    prefix, count = Counter(prefixes).most_common(1)[0]
    return prefix if count >= 3 and count >= 0.75 * len(prefixes) else None


def _check_repeated_prefix(results, crops):
    prefix = _page_prefix(results)
    if not prefix:
        return
    for record, crop in zip(results, crops):
        alternatives = record["glyph_alternatives"]
        if not alternatives or crop is None:
            continue
        suffixes = _observed_suffixes(record["crop_passes"])
        checked = rank_tag_glyphs(crop, suffixes, prefix=prefix)
        record["repeated_prefix_alternatives"] = checked
        if not checked or checked[0]["mismatch"] - alternatives[0]["mismatch"] > 0.035:
            continue
        best = checked[0]
        gap = checked[1]["mismatch"] - best["mismatch"] if len(checked) > 1 else float("inf")
        if best["mismatch"] <= 0.29 and gap >= 0.002:
            record.update(observed_tag=best["text"],
                          ocr_selection="segmented glyph comparison with prefix read elsewhere on the same drawing",
                          uncertainty=None)
        if record["observed_tag"] is not None and gap >= 0.002:
            continue
        variants = {"thin": crop.filter(ImageFilter.MaxFilter(3)),
                    "wide": crop.resize((round(crop.width * 1.15), crop.height), Image.Resampling.LANCZOS)}
        readings = {name: rank_tag_glyphs(view, suffixes, prefix=prefix) for name, view in variants.items()}
        record["glyph_variant_alternatives"] = readings
        close = {reading["text"] for reading in checked if reading["mismatch"] <= best["mismatch"] + 0.003}
        supported = set()
        for reading in readings.values():
            if not reading or reading[0]["text"] not in close or reading[0]["mismatch"] > 0.28:
                continue
            if len(reading) == 1 or reading[1]["mismatch"] - reading[0]["mismatch"] >= 0.003:
                supported.add(reading[0]["text"])
        if not supported and readings["thin"] and readings["thin"][0]["text"] == best["text"]:
            thin = readings["thin"]
            if thin[0]["mismatch"] <= 0.28 and (len(thin) == 1 or thin[1]["mismatch"] - thin[0]["mismatch"] >= 0.002):
                supported.add(best["text"])
        if len(supported) == 1:
            record.update(observed_tag=supported.pop(), ocr_selection="glyph reading corroborated by an alternate pixel preparation",
                          uncertainty=None)


def recognize_label_boxes(image_path, labels, *, scratch_dir=None, timeout=90):
    """Read text from supplied image-space regions without receiving expected values."""
    context = tempfile.TemporaryDirectory(prefix="aip-labels-") if scratch_dir is None else nullcontext(str(scratch_dir))
    with context as temp, Image.open(image_path) as source:
        image = source.convert("L")
        Path(temp).mkdir(parents=True, exist_ok=True)
        results = []
        crops = []
        for index, label in enumerate(labels):
            box = label.get("tag_bbox")
            passes = []
            prefix = None
            crop = None
            if box is not None:
                left, top, right, bottom = box
                if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                    raise OCRUnavailable("Label crop is outside the image.")
                crop = image.crop(tuple(map(round, box)))
                expanded = image.crop((max(0, math.floor(left) - 2), max(0, math.floor(top) - 2),
                                       min(image.width, math.ceil(right) + 2), min(image.height, math.ceil(bottom) + 2)))
                variants = ((crop, (1, 1), 8), (crop, (2, 2), 8), (expanded, (1, 1.5), 8),
                            (expanded, (2, 2), 8), (expanded, (3, 3), 8))
                for trial, (view, scale, pad) in enumerate(variants):
                    passes.append(_text_pass(view, scale, pad, f"box-{index}-{trial}", temp, timeout))
                prefix, suffix = _prefix_and_suffix(crop)
                if prefix:
                    for trial, scale in enumerate((1, 2, 3)):
                        result = _text_pass(suffix, (scale, scale), 12, f"box-{index}-suffix-{trial}", temp, timeout)
                        passes.append({**result, "suffix": True})
            observed, selection = _choose_box_text(passes, prefix)
            rankings = []
            if crop is not None and (not observed or (_NUMBERED_TAG.fullmatch(observed) and not prefix)):
                rankings = rank_tag_glyphs(crop, _observed_suffixes(passes))
                if rankings:
                    best = rankings[0]
                    gap = rankings[1]["mismatch"] - best["mismatch"] if len(rankings) > 1 else float("inf")
                    if best["mismatch"] <= 0.29 and gap >= 0.002:
                        observed = best["text"]
                        selection = "segmented glyph comparison on the scanned label"
            results.append({"component_id": label["id"], "observed_tag": observed,
                            "source_word_bbox": box, "native_text": passes[0]["text"] if passes else None,
                            "ocr_selection": selection, "crop_passes": passes, "glyph_alternatives": rankings,
                            "uncertainty": None if observed else "No consistent label reading from the supplied image region."})
            crops.append(crop)
        _check_repeated_prefix(results, crops)
    return results


def recognize_tag_crops(image_path, initial=None, timeout=90, max_candidates=16, scratch_dir=None):
    """Preserve native words and add auditable crop OCR alternatives.

    Every word matching a generic prefix/separator/suffix pattern is considered.
    The suffix crop is found from short horizontal separator ink, with no known
    tag values. A candidate changes only when 1x and 3x suffix OCR separately
    executed on the same pixels agree in text and length; this is correlated
    development evidence, not calibrated confidence or an independent test.
    """
    crop_passes = []
    if scratch_dir is not None:
        scratch = Path(scratch_dir).resolve()
        scratch.mkdir(parents=True,exist_ok=True)
        if any(scratch.iterdir()):
            raise OCRUnavailable("OCR scratch directory must be new or empty; previous crop evidence is preserved.")
        context = nullcontext(str(scratch))
    else:
        context = tempfile.TemporaryDirectory(prefix="aip-ocr-")
    with context as temp:
        # Keep crop pixels and raw OCR evidence outside the input packet.
        result = initial if initial is not None else recognize_image(image_path, timeout,working_directory=temp)
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        candidates = [w for w in result["words"] if w.get("bbox") is not None and re.fullmatch(r"[A-Za-z]{1,6}[-/][A-Za-z0-9]{1,10}", w["text"])]
        if scratch_dir is not None:
            (Path(temp)/"native.ocr.json").write_text(json.dumps(result,indent=2,ensure_ascii=True,allow_nan=False)+"\n",encoding="utf-8")
        for index, word in enumerate(candidates[:max_candidates]):
            match = re.fullmatch(r"([A-Za-z]{1,6})([-/])([A-Za-z0-9]{1,10})", word["text"])
            suffix_box = _separator_suffix_box(image, word["bbox"])
            record = {"original_text": word["text"], "source_word_bbox": word["bbox"], "suffix_crop_bbox": suffix_box,
                      "passes": [], "selected_text": word["text"], "selection": "native word retained"}
            if suffix_box is not None:
                crop = image.crop(suffix_box)
                for scale in (1,3):
                    view = crop.resize((crop.width*scale,crop.height*scale),Image.Resampling.LANCZOS)
                    filename = Path(temp) / f"suffix-{index}-{scale}.png"
                    view.save(filename)
                    alternative = recognize_image(filename, timeout,working_directory=temp)
                    text = "".join(w["text"] for w in alternative["words"])
                    pass_record = {"scale": scale, "threshold": None, "text": text, "ocr": alternative}
                    if scratch_dir is not None:
                        ocr_filename = filename.with_suffix(".ocr.json")
                        ocr_filename.write_text(json.dumps(alternative,indent=2,ensure_ascii=True,allow_nan=False)+"\n",encoding="utf-8")
                        pass_record["crop_image_filename"] = filename.name
                        pass_record["ocr_json_filename"] = ocr_filename.name
                    record["passes"].append(pass_record)
                texts = [p["text"] for p in record["passes"]]
                if (texts[0] == texts[1] and re.fullmatch(r"[A-Za-z0-9]+",texts[0])
                        and len(texts[0]) == len(match[3])):
                    record["selected_text"] = match[1]+match[2]+texts[0]
                    record["selection"] = "two suffix-crop OCR passes agree; no alphabet restriction, whitelist or reference correction"
            crop_passes.append(record)
    return {"native": result, "tag_crop_passes": crop_passes, "crop_artifacts_retained": scratch_dir is not None,
            "method": "native full-image OCR plus separator-ink suffix crops at 1x and 3x; all original observations retained",
            "warning": "Crop agreement is correlated OCR evidence, not confidence or human verification; this is development on a synthetic image."}
