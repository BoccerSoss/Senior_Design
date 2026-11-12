"""Build a pixel-coordinate record and render the same markings on a drawing."""
from __future__ import annotations

from math import ceil, floor

from PIL import ImageDraw, ImageFont

UNKNOWN_DIRECTION = {"status": "unknown", "arrow_tip": None, "arrow_wings": None}
SYMBOL_COLOR = "#b00020"
TAG_COLOR = "#7143a0"
PIPE_COLOR = "#087e8b"
ARROW_COLOR = "#12624f"
LABEL_SIZE = 18


def _raster_box(box):
    return [floor(box[0]), floor(box[1]), ceil(box[2]), ceil(box[3])]


def _raster_points(points, width, height):
    return [[min(width - 1, round(x)), min(height - 1, round(y))] for x, y in points]


def _label_mark(component_id, text, box, draw, font, width, height):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_width, text_height = right - left, bottom - top
    x = min(box[0], max(0, width - text_width))
    y = min(max(0, box[1] - 24), max(0, height - text_height))
    return {"kind": "label", "component_id": component_id, "text": text,
            "anchor": [x - left, y - top],
            "bbox": [x, y, min(width, x + text_width), min(height, y + text_height)],
            "font_size": LABEL_SIZE, "color": SYMBOL_COLOR, "background": "white"}


def _draw_mark(draw, mark, font):
    kind = mark["kind"]
    if kind in {"pipe_trace", "flow_arrow"}:
        draw.line([tuple(point) for point in mark["points"]], fill=mark["color"], width=mark["stroke_width"])
        return
    x0, y0, x1, y1 = mark["bbox"]
    inclusive = (x0, y0, x1 - 1, y1 - 1)
    if kind == "label":
        draw.rectangle(inclusive, fill=mark["background"])
        draw.text(tuple(mark["anchor"]), mark["text"], font=font, fill=mark["color"])
    else:
        draw.rectangle(inclusive, outline=mark["color"], width=mark["stroke_width"])


def build_markings(drawing, equipment, edges, labels, image=None):
    """Record observations, plus the exact draw commands if an overlay is rendered."""
    width, height = drawing["width"], drawing["height"]
    record = {
        "schema_version": "1.0.0",
        "drawing": dict(drawing),
        "coordinates": {
            "origin": "top-left", "units": "prepared image pixels", "axes": "x right, y down",
            "point_format": ["x", "y"], "box_format": ["xmin", "ymin", "xmax", "ymax"],
            "box_bounds": "minimum inclusive, maximum exclusive",
        },
        "overlay_file": "overlay.png" if image is not None else None,
        "components": [],
        "connections": [],
        "marks": [],
    }
    marks = record["marks"]
    for edge in edges:
        source, target = edge["source"], edge["target"]
        path = edge.get("path")
        direction = dict(edge.get("direction", UNKNOWN_DIRECTION))
        flow = None
        if direction["status"] == "source_to_target":
            flow = [source, target]
        elif direction["status"] == "target_to_source":
            flow = [target, source]
        direction["from"] = flow[0] if flow else None
        direction["to"] = flow[1] if flow else None
        record["connections"].append({"source": source, "target": target,
                                      "source_label": labels[source], "target_label": labels[target],
                                      "path": path, "direction": direction})
        if image is not None and path is not None:
            marks.append({"kind": "pipe_trace", "connection": [source, target],
                          "points": _raster_points(path, width, height), "color": PIPE_COLOR, "stroke_width": 2})
            if flow:
                wing_a, wing_b = direction["arrow_wings"]
                arrow = [wing_a, direction["arrow_tip"], wing_b]
                marks.append({"kind": "flow_arrow", "connection": [source, target],
                              "points": _raster_points(arrow, width, height), "color": ARROW_COLOR, "stroke_width": 3})
    draw = ImageDraw.Draw(image) if image is not None else None
    font = ImageFont.load_default(size=LABEL_SIZE) if draw is not None else None
    for item in equipment:
        component_id = item["id"]
        bbox, tag_bbox = item["bbox"], item.get("tag_bbox")
        record["components"].append({"id": component_id, "label": labels[component_id], "type": item["type"],
                                     "bbox": bbox, "tag_bbox": tag_bbox})
        if draw is None:
            continue
        if tag_bbox is not None:
            marks.append({"kind": "tag_outline", "component_id": component_id, "bbox": _raster_box(tag_bbox),
                          "color": TAG_COLOR, "stroke_width": 2})
        if bbox is not None:
            box = _raster_box(bbox)
            marks.append({"kind": "symbol_outline", "component_id": component_id, "bbox": box,
                          "color": SYMBOL_COLOR, "stroke_width": 3})
            marks.append(_label_mark(component_id, f"{labels[component_id]} ({item['type']})",
                                     box, draw, font, width, height))
    for mark in marks:
        _draw_mark(draw, mark, font)
    return record
