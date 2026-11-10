"""Optional local Windows.Media.Ocr adapter; input raster pixels only.

No provider login, network access, source vector, reference, or expected labels
are used. The Windows OCR language capability is an OS prerequisite, not a pip
dependency. API words do not include confidence, so it is recorded as null.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
import tempfile
from contextlib import nullcontext
from pathlib import Path

from PIL import Image


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

    Supply a prepared RGB PNG from the image-only packet. PowerShell receives
    only this image path. OCR boxes are transformed clockwise by TextAngle about
    the image center, following Microsoft's documented original-image overlay.
    """
    if sys.platform != "win32":
        raise OCRUnavailable("Windows.Media.Ocr requires Windows 10/11 with Windows PowerShell 5.1 and an installed OCR language; use the baseline or manual importer on this platform.")
    image = Path(image_path).resolve()
    if not image.is_file():
        raise OCRUnavailable(f"OCR input image does not exist: {image.name}")
    if image.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise OCRUnavailable("Windows OCR adapter accepts a prepared PNG/JPEG raster only.")
    helper = Path(__file__).resolve().parents[2] / "scripts/windows_ocr.ps1"
    if not helper.is_file():
        raise OCRUnavailable("Windows OCR helper is missing; restore scripts/windows_ocr.ps1 from this checkout.")
    windows_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = windows_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        raise OCRUnavailable("Windows PowerShell 5.1 powershell.exe is unavailable; PowerShell Core alone cannot run this WinRT helper.")
    working = Path(working_directory).resolve() if working_directory is not None else image.parent
    if not working.is_dir():
        raise OCRUnavailable("Windows OCR working directory must be an existing private scratch directory.")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    started = time.perf_counter()
    try:
        process = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(helper), "-ImagePath", str(image)],
            cwd=working, capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup,
        )
    except subprocess.TimeoutExpired as exc:
        raise OCRUnavailable(f"Windows OCR timed out after {timeout} seconds; the response is unavailable and was not replaced with a fixture.") from exc
    except OSError as exc:
        raise OCRUnavailable(f"Windows OCR process could not start: {exc}") from exc
    stdout = process.stdout.decode("utf-8-sig", errors="replace").strip()
    stderr = process.stderr.decode("utf-8-sig", errors="replace").strip()
    try:
        result = json.loads(stdout)
    except (ValueError, TypeError) as exc:
        detail = stderr or stdout or "no output"
        raise OCRUnavailable(f"Windows OCR returned no valid JSON (exit {process.returncode}): {detail[:1000]}") from exc
    if process.returncode or not isinstance(result, dict) or result.get("available") is not True:
        detail = result.get("error", stderr or f"helper exit {process.returncode}") if isinstance(result, dict) else "helper returned a non-object response"
        raise OCRUnavailable(f"Windows OCR unavailable: {detail}")
    dimensions = result.get("image")
    if not isinstance(dimensions, dict) or any(isinstance(dimensions.get(k), bool) or not isinstance(dimensions.get(k), int) or dimensions[k] <= 0 for k in ("width", "height")):
        raise OCRUnavailable("Windows OCR returned invalid image dimensions; no coordinates were accepted.")
    width, height = dimensions["width"], dimensions["height"]
    if not isinstance(result.get("words"), list) or not isinstance(result.get("lines"), list):
        raise OCRUnavailable("Windows OCR returned malformed word/line arrays.")
    angle = result.get("text_angle_degrees")
    if angle is not None and (isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle)):
        raise OCRUnavailable("Windows OCR returned invalid TextAngle; no coordinate transform was applied.")
    def convert_word(word):
        if not isinstance(word, dict) or not isinstance(word.get("text"), str):
            raise OCRUnavailable("Windows OCR returned a malformed word observation.")
        box = word.get("bbox")
        if box is not None:
            if (not isinstance(box, list) or len(box) != 4 or
                    any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box) or
                    not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
                raise OCRUnavailable("Windows OCR returned an unsupported or out-of-bounds word box; coordinates were not repaired.")
            word["engine_bbox"] = box
            word["bbox"], word["polygon"] = _original_box(box, angle, width, height)
            if word["bbox"] is None:
                result["warnings"].append("An OCR box transformed outside the image; original-coordinate bbox withheld, raw engine box/polygon retained.")
        if word.get("confidence") is not None:
            raise OCRUnavailable("Windows.Media.Ocr unexpectedly reported word confidence; this adapter has no documented confidence mapping.")
    for word in result["words"]:
        convert_word(word)
    for line in result["lines"]:
        if not isinstance(line, dict) or not isinstance(line.get("words"), list) or not isinstance(line.get("text"), str):
            raise OCRUnavailable("Windows OCR returned a malformed line observation.")
        for word in line["words"]:
            convert_word(word)
        boxes = [w["bbox"] for w in line["words"] if w["bbox"] is not None]
        line["engine_bbox"] = line["bbox"]
        line["bbox"] = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None
    result["coordinate_convention"] = "top-left original image pixels; half-open axis-aligned enclosures after documented clockwise TextAngle rotation around image center; engine boxes/polygons retained"
    result["duration_seconds"] = round(time.perf_counter() - started, 6)
    return result


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
        # Native Windows/PowerShell startup may create profile/cache paths in
        # cwd. Keep even the first full-image pass outside the input packet.
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
