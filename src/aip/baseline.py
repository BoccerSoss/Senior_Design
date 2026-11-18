"""Conservative, image-only linework baseline using Pillow.

This method detects large connected linework, separates symbol-like regions
after suppressing long pipe strokes, and reports uninterrupted horizontal
contacts. Ambiguous stacked regions are separated using both image axes.
It neither recognizes text nor assigns semantic equipment types.
Nothing in this module reads answer files, source drawings, or expected tags.
"""
from __future__ import annotations

from pathlib import Path
import re

from PIL import Image, ImageOps, __version__ as pillow_version

from .connection_geometry import flow_direction, horizontal_stroke, is_pipe_arrow_box, observe_flow_arrows
from .flow_routes import trace_arrow_routes


def baseline_info():
    """Describe actual capabilities without implying OCR or VLM inference."""
    return {
        "method": "pillow-linework-baseline-v2",
        "provider": "local",
        "model": None,
        "access_route": "local deterministic raster processing",
        "pillow_version": pillow_version,
        "ocr_available": False,
        "scope": "unlabeled symbol candidates, horizontal contacts, visible arrowheads and candidate flow routes",
        "limitations": [
            "No OCR: observed tags remain null.",
            "No semantic symbol classifier: type remains unknown.",
            "Only horizontal direct contacts are traced; bends and branches need review.",
            "Routes trace a supported shaft through connected symbol ink and stop at an unresolved branch or break.",
            "Large connected text or unusual linework can be misidentified.",
        ],
    }


def connected_ink_spans(mask, width, height):
    """Flood connected row spans. Diagonal pixel contacts count as connected."""
    remaining = bytearray(mask)
    ink = re.compile(rb"[^\x00]")

    def take_span(seed):
        row = seed // width
        start = row * width
        stop = start + width
        left = remaining.rfind(b"\x00", start, seed)
        left = start if left == -1 else left + 1
        right = remaining.find(b"\x00", seed, stop)
        right = stop if right == -1 else right
        remaining[left:right] = bytes(right - left)
        return row, left - start, right - start

    cursor = 0
    while first := ink.search(remaining, cursor):
        cursor = first.start() + 1
        spans = [take_span(first.start())]
        # Each span is removed when queued, so adjacent rows cannot queue it twice.
        for row, left, right in spans:
            for adjacent in (row - 1, row + 1):
                if 0 <= adjacent < height:
                    offset = adjacent * width
                    start = offset + max(0, left - 1)
                    stop = offset + min(width, right + 1)
                    while found := ink.search(remaining, start, stop):
                        span = take_span(found.start())
                        spans.append(span)
                        start = offset + span[2]
        xmin = min(left for _, left, _ in spans)
        xmax = max(right for _, _, right in spans)
        ymin = min(row for row, _, _ in spans)
        ymax = max(row for row, _, _ in spans) + 1
        yield spans, (xmin, ymin, xmax, ymax)


def _ink_components(mask, width, height):
    minimum_w = max(36, round(width * 0.02))
    minimum_h = max(36, round(height * 0.045))
    for spans, (xmin, ymin, xmax, ymax) in connected_ink_spans(mask, width, height):
        if xmax - xmin >= minimum_w and ymax - ymin >= minimum_h:
            yield [(x, row) for row, left, right in spans for x in range(left, right)], (xmin, ymin, xmax, ymax)


def _suppress_horizontal(points, width, preserve_rectangles=True):
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
                elif preserve_rectangles:
                    suppressed.setdefault((xs[start], xs[end-1]), []).append(y)
                start = end
    # Equal-span horizontal edges separated vertically are evidence of a closed
    # rectangle, unlike the few neighboring rows making up a single pipe stroke.
    for (left, right), ys in suppressed.items():
        if max(ys)-min(ys) >= 28:
            retained.extend((x, y) for y in ys for x in range(left, right+1))
    return retained


def _column_boxes(points, width):
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


def _candidate_boxes(points, width):
    boxes = _column_boxes(points, width)
    # Separate rows only when projection has collapsed a large portion of the
    # network into one column. Ordinary single-row tank and valve outlines stay intact.
    ambiguous = any(max(right - left, bottom - top) > width * 0.25
                    or bottom - top > max(3 * (right - left), width * 0.09)
                    for left, top, right, bottom in boxes)
    if not ambiguous:
        return boxes
    horizontal = _suppress_horizontal(points, width, preserve_rectangles=False)
    transposed = [(y, x) for x, y in points]
    vertical = {(x, y) for y, x in _suppress_horizontal(transposed, width, preserve_rectangles=False)}
    # Neighboring occupied cells bridge the small gaps cut through valve bodies
    # by shaft removal, while disconnected rows in the same column stay separate.
    cell_size = max(5, round(width * 0.002))
    cells = {}
    for x, y in horizontal:
        if (x, y) in vertical:
            cells.setdefault((x // cell_size, y // cell_size), []).append((x, y))
    local = []
    while cells:
        seed, pixels = cells.popitem()
        pending = [seed]
        for col, row in pending:
            for xx in range(col - 1, col + 2):
                for yy in range(row - 1, row + 2):
                    neighbor = (xx, yy)
                    group = cells.pop(neighbor, None)
                    if group:
                        pending.append(neighbor)
                        pixels.extend(group)
        xs, ys = zip(*pixels)
        left, top, right, bottom = min(xs), min(ys), max(xs) + 1, max(ys) + 1
        side, span = sorted((right - left, bottom - top))
        if (side >= max(18, width * 0.008) and span <= min(4 * side, width * 0.2)
                and len(pixels) >= side * 2.3):
            local.append([left, top, right, bottom])
    # Axis suppression can remove the straight sides of an enclosed vessel or
    # valve body. Retain a compact column box with no or one near-identical local box.
    for box in boxes:
        left, top, right, bottom = box
        w, h = right - left, bottom - top
        # A standalone rectangle can lose all four sides during axis suppression.
        # Preserve an isolated original enclosure unless its extents are frame-sized.
        width_limit = 0.55 if len(boxes) == 1 and not local else 0.12
        if max(w, h) > min(width * width_limit, 4 * min(w, h)):
            continue
        contained = []
        for candidate in local:
            x0, y0, x1, y1 = candidate
            overlap = max(0, min(x1, right) - max(x0, left)) * max(0, min(y1, bottom) - max(y0, top))
            area = (x1 - x0) * (y1 - y0)
            if overlap > area / 2:
                contained.append((candidate, overlap / (w * h + area - overlap)))
        if not contained:
            local.append(box)
        elif len(contained) == 1 and contained[0][1] > 0.7:
            local[local.index(contained[0][0])] = box
    return local


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


def _drawing_arrows(mask, width, connections, arrows):
    for edge in connections:
        flow = edge["direction"]
        if flow["status"] == "unknown":
            continue
        tip = flow["arrow_tip"]
        heading = "right" if flow["status"] == "source_to_target" else "left"
        if any(arrow["direction"] == heading and
               max(abs(x - y) for x, y in zip(arrow["arrow_tip"], tip)) <= 10 for arrow in arrows):
            continue
        arrows.append({"arrow_tip": tip, "arrow_wings": flow["arrow_wings"], "direction": heading})
    arrows.sort(key=lambda arrow: (arrow["arrow_tip"][1], arrow["arrow_tip"][0], arrow["direction"]))
    return [{"id": f"arrow-{index:03d}", **arrow} for index, arrow in enumerate(arrows, 1)]


def extract_image(image_path, *, refine_boxes=None):
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
    arrow_marks = observe_flow_arrows(mask, width)
    if refine_boxes is not None:
        boxes = refine_boxes(gray, boxes, arrow_marks)
    boxes.sort(key=lambda b: (b[0], b[1], b[2], b[3]))
    components = []
    for index, box in enumerate(boxes, 1):
        components.append({
            "id": f"candidate-{index:03d}",
            "observed_tag": None,
            "type": "unknown",
            "bbox": box,
            "evidence": "Image-derived symbol-like region after suppression of long pipe strokes; box is approximate.",
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
    arrows = _drawing_arrows(mask, width, connections, arrow_marks)
    routes = trace_arrow_routes(mask, width, arrows, components)
    uncertainties = [
        "Local geometry baseline has no OCR or semantic symbol classifier; tags remain null and types unknown.",
        "Direct contacts use uninterrupted horizontal strokes. Flow routes stop at unresolved branches, crossings, and breaks.",
        "Candidate counts and connections are provisional image observations, not independently checked equipment records.",
    ]
    if not components:
        uncertainties.append("No supported symbol-like regions were detected; the image may be blank, unsuitable or outside baseline capabilities.")
    return {
        "image": {"width": width, "height": height},
        "components": components,
        "connections": connections,
        "arrows": arrows,
        "flow_routes": routes,
        "uncertainties": uncertainties,
    }
