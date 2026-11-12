"""Build a pixel-coordinate record and render the same markings on a drawing."""
from __future__ import annotations

from math import ceil, floor

from PIL import ImageDraw, ImageFont

UNKNOWN_DIRECTION = {"status": "unknown", "arrow_tip": None, "arrow_wings": None}
SYMBOL_COLOR = "#b00020"
TAG_COLOR = "#7143a0"
PIPE_COLOR = "#087e8b"
ARROW_COLOR = "#12624f"
ROUTE_COLOR = "#2461b5"
LABEL_SIZE = 18


def _raster_box(box):
    return [floor(box[0]), floor(box[1]), ceil(box[2]), ceil(box[3])]


def _raster_points(points, width, height):
    return [[min(width - 1, round(x)), min(height - 1, round(y))] for x, y in points]


def _label_mark(component_id, text, box, draw, font, width, height, *, below=False, clear_box=None):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_width, text_height = right - left, bottom - top
    target_x = (box[0] + box[2] - text_width) // 2 if below else box[0]
    x = max(0, min(target_x, width - text_width))
    target_y = box[3] + 4 if below else box[1] - 24
    y = min(max(0, target_y), max(0, height - text_height))
    if clear_box and x < clear_box[2] and clear_box[0] < x + text_width and y < clear_box[3] and clear_box[1] < y + text_height:
        y = max(0, clear_box[1] - text_height - 3)
    return {"kind": "equipment_grid_label" if below else "label", "component_id": component_id, "text": text,
            "anchor": [x - left, y - top],
            "bbox": [x, y, min(width, x + text_width), min(height, y + text_height)],
            "font_size": LABEL_SIZE, "color": SYMBOL_COLOR, "background": "white"}


def _draw_mark(draw, mark, font):
    kind = mark["kind"]
    if kind in {"pipe_trace", "flow_arrow", "flow_route", "observed_arrow"}:
        draw.line([tuple(point) for point in mark["points"]], fill=mark["color"], width=mark["stroke_width"])
        return
    x0, y0, x1, y1 = mark["bbox"]
    inclusive = (x0, y0, x1 - 1, y1 - 1)
    if kind in {"label", "equipment_grid_label"}:
        draw.rectangle(inclusive, fill=mark["background"])
        draw.text(tuple(mark["anchor"]), mark["text"], font=font, fill=mark["color"])
    else:
        draw.rectangle(inclusive, outline=mark["color"], width=mark["stroke_width"])


def _same_arrow(first, second):
    tip_a, tip_b = first["arrow_tip"], second["arrow_tip"]
    if max(abs(a - b) for a, b in zip(tip_a, tip_b)) > 10:
        return False
    axes = []
    for arrow in (first, second):
        wing_a, wing_b = arrow["arrow_wings"]
        axes.append([2 * tip - a - b for tip, a, b in zip(arrow["arrow_tip"], wing_a, wing_b)])
    return sum(a * b for a, b in zip(*axes)) > 0


def build_markings(drawing, equipment, edges, labels, image=None, *, arrows=(), flow_routes=()):
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
        "arrows": list(arrows),
        "flow_routes": [{**route, "component_labels": [labels[ident] for ident in route["component_ids"]]}
                        for route in flow_routes],
        "marks": [],
    }
    marks = record["marks"]
    arrow_marks = []
    routed_arrows = {route["arrow_id"] for route in flow_routes}
    linked_arrows = set()
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
                mark = {"kind": "flow_arrow", "connection": [source, target],
                        "points": _raster_points(arrow, width, height), "color": ARROW_COLOR, "stroke_width": 3}
                match = next((a for a in arrows if a["id"] not in linked_arrows and _same_arrow(a, direction)), None)
                if match:
                    linked_arrows.add(match["id"])
                    wing_a, wing_b = match["arrow_wings"]
                    mark.update(arrow_id=match["id"], direction=match["direction"],
                                points=_raster_points([wing_a, match["arrow_tip"], wing_b], width, height))
                    if match["id"] in routed_arrows:
                        mark["route_id"] = match["id"]
                arrow_marks.append(mark)
    if image is not None:
        for route in record["flow_routes"]:
            marks.append({"kind": "flow_route", "route_id": route["arrow_id"], "arrow_id": route["arrow_id"],
                          "points": _raster_points(route["path"], width, height),
                          "component_ids": route["component_ids"], "component_labels": route["component_labels"],
                          "color": ROUTE_COLOR, "stroke_width": 5})
        for arrow in arrows:
            arrow_id = arrow["id"]
            if arrow_id in linked_arrows:
                continue
            wings = arrow["arrow_wings"]
            mark = {"kind": "observed_arrow", "arrow_id": arrow_id, "direction": arrow["direction"],
                    "points": _raster_points([wings[0], arrow["arrow_tip"], wings[1]], width, height),
                    "color": ARROW_COLOR, "stroke_width": 6}
            if arrow_id in routed_arrows:
                mark["route_id"] = arrow_id
            arrow_marks.append(mark)
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
                                     box, draw, font, width, height,
                                     clear_box=_raster_box(tag_bbox) if tag_bbox is not None else None))
            if item.get("grid"):
                column, row = item["grid"]
                bottom = max(box[3], ceil(tag_bbox[3]) if tag_bbox else box[3])
                anchor = [box[0], box[1], box[2], bottom]
                marks.append(_label_mark(component_id, f"({column},{row})", anchor,
                                         draw, font, width, height, below=True))
    marks.extend(arrow_marks)
    for mark in marks:
        _draw_mark(draw, mark, font)
    return record
