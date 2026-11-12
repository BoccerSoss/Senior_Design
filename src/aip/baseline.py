"""Conservative, image-only linework baseline using Pillow.

This method detects large connected linework, separates symbol-like x-regions
after suppressing long horizontal strokes, and reports uninterrupted horizontal
contacts. It neither recognizes text nor assigns semantic equipment types.
Nothing in this module reads answer files, source drawings, or expected tags.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image, ImageOps, __version__ as pillow_version

from .connection_geometry import flow_direction, horizontal_stroke, is_pipe_arrow_box


def baseline_info():
    """Describe actual capabilities without implying OCR or VLM inference."""
    return {
        "method": "pillow-linework-baseline-v1",
        "provider": "local",
        "model": None,
        "access_route": "local deterministic raster processing",
        "pillow_version": pillow_version,
        "ocr_available": False,
        "scope": "unlabeled symbol candidates, uninterrupted horizontal pipe contacts, and visible flow arrows on supported pipes",
        "limitations": [
            "No OCR: observed tags remain null.",
            "No semantic symbol classifier: type remains unknown.",
            "Only horizontal direct contacts are traced; bends and branches need review.",
            "Flow direction requires a visible paired-wing arrow on the pipe span.",
            "Large connected text or unusual linework can be misidentified.",
        ],
    }


def _ink_components(mask, width, height):
    """Find 8-connected foreground groups from an image-derived binary mask."""
    remaining = bytearray(mask)
    groups = []
    for seed, value in enumerate(remaining):
        if not value:
            continue
        remaining[seed] = 0
        queue = deque([seed])
        points = []
        xmin, ymin, xmax, ymax = width, height, 0, 0
        while queue:
            p = queue.popleft()
            y, x = divmod(p, width)
            points.append((x, y))
            xmin, ymin = min(xmin, x), min(ymin, y)
            xmax, ymax = max(xmax, x), max(ymax, y)
            for ny in range(max(0, y-1), min(height, y+2)):
                offset = ny * width
                for nx in range(max(0, x-1), min(width, x+2)):
                    nxt = offset + nx
                    if remaining[nxt]:
                        remaining[nxt] = 0
                        queue.append(nxt)
        if xmax-xmin+1 >= max(36, round(width * 0.02)) and ymax-ymin+1 >= max(36, round(height * 0.045)):
            groups.append((points, (xmin, ymin, xmax+1, ymax+1)))
    return groups


def _suppress_horizontal(points, width):
    """Remove long horizontal runs, which include pipe and some symbol strokes."""
    rows = {}
    for x, y in points:
        rows.setdefault(y, []).append(x)
    retained = []
    suppressed = {}
    minimum = max(32, round(width * 0.025))
    for y, xs in rows.items():
        xs.sort()
        start = 0
        for end in range(1, len(xs)+1):
            if end == len(xs) or xs[end] != xs[end-1]+1:
                if end-start < minimum:
                    retained.extend((x, y) for x in xs[start:end])
                else:
                    suppressed.setdefault((xs[start], xs[end-1]), []).append(y)
                start = end
    # Equal-span horizontal edges separated vertically are evidence of a closed
    # rectangle, unlike the few neighboring rows making up a single pipe stroke.
    for (left, right), ys in suppressed.items():
        if max(ys)-min(ys) >= 28:
            retained.extend((x, y) for y in ys for x in range(left, right+1))
    return retained


def _candidate_boxes(points, width):
    kept = _suppress_horizontal(points, width)
    columns = {}
    for x, y in kept:
        columns.setdefault(x, []).append(y)
    active = sorted(x for x, ys in columns.items() if len(ys) >= 2)
    if not active:
        return []
    intervals, first, last = [], active[0], active[0]
    for x in active[1:]:
        if x-last > 4:
            intervals.append((first, last))
            first = x
        last = x
    intervals.append((first, last))
    boxes = []
    for left, right in intervals:
        if right-left+1 < max(18, round(width * 0.008)):
            continue
        ys = [y for x in range(left, right+1) for y in columns.get(x, ())]
        if not ys or max(ys)-min(ys)+1 < 28:
            continue
        boxes.append([left, min(ys), right+1, max(ys)+1])
    return boxes


def _horizontal_contact(mask, width, left, right):
    """Return evidence for a continuous raster stroke between two candidate boxes."""
    ax0, ay0, ax1, ay1 = left
    bx0, by0, bx1, by1 = right
    if bx0 <= ax1:
        return None
    top, bottom = max(ay0, by0), min(ay1, by1)
    if bottom <= top:
        return None
    # Include the candidate border at both ends; mere proximity fails.
    return horizontal_stroke(mask, width, ax1 - 1, bx0, top, bottom)


def extract_image(image_path):
    """Consume raster pixels and return observations, including explicit uncertainty.

    Filesystem access is restricted by design to the supplied image path. This
    function is not an OS sandbox; the CLI separately stages inference inputs.
    """
    path = Path(image_path)
    with Image.open(path) as source:
        if source.format not in {"PNG", "JPEG"}:
            raise ValueError("The local baseline supports PNG/JPEG only; render other formats first.")
        image = ImageOps.exif_transpose(source).convert("RGB")
    width, height = image.size
    gray = image.convert("L")
    mask = gray.point(lambda v: 1 if v < 160 else 0).tobytes()
    boxes = []
    for points, _ in _ink_components(mask, width, height):
        boxes.extend(box for box in _candidate_boxes(points, width)
                     if not is_pipe_arrow_box(mask, width, box))
    boxes.sort(key=lambda b: (b[0], b[1], b[2], b[3]))
    components = []
    for index, box in enumerate(boxes, 1):
        components.append({
            "id": f"candidate-{index:03d}",
            "observed_tag": None,
            "type": "unknown",
            "bbox": box,
            "evidence": "Image-derived symbol-like region after long horizontal stroke suppression; box is approximate.",
            "uncertainty": "Unverified geometric candidate. OCR and semantic classification unavailable; tag and type unresolved.",
        })
    connections = []
    for i, left in enumerate(boxes):
        for j in range(i+1, len(boxes)):
            contact = _horizontal_contact(mask, width, left, boxes[j])
            if contact is not None:
                connections.append({
                    "source": components[i]["id"],
                    "target": components[j]["id"],
                    "kind": "direct_undirected",
                    "path": contact.path,
                    "direction": flow_direction(mask, width, contact),
                    "evidence": f"Uninterrupted dark horizontal raster stroke between candidate borders at {contact.path}.",
                    "uncertainty": "Geometric contact only; candidate identity and process-line interpretation require review.",
                })
    uncertainties = [
        "Local geometry baseline has no OCR or semantic symbol classifier; tags remain null and types unknown.",
        "Only large linework regions and uninterrupted horizontal contacts are supported; bends, branches and crossings are unresolved.",
        "Candidate counts and connections are provisional image observations, not independently checked equipment records.",
    ]
    if not components:
        uncertainties.append("No supported symbol-like regions were detected; the image may be blank, unsuitable or outside baseline capabilities.")
    return {
        "image": {"width": width, "height": height},
        "components": components,
        "connections": connections,
        "uncertainties": uncertainties,
    }
