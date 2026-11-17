"""A nonuniform source-image grid fitted through exact symbol centers."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from math import isfinite


MAX_AXIS = 200


def grid_settings(value=None):
    value = {} if value is None else value
    if not isinstance(value, Mapping):
        raise ValueError("Grid settings must be an object.")
    result = {}
    for field, default in (("columns", 12), ("rows", 16)):
        count = value.get(field, default)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_AXIS:
            raise ValueError("Grid rows and columns must be whole numbers from 1 to 200.")
        result[field] = count
    result["origin"] = "top-left"
    return result


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _anchor(box, width, height):
    if (not _number(width) or not _number(height) or width <= 0 or height <= 0 or
            not isinstance(box, (list, tuple)) or len(box) != 4 or not all(_number(v) for v in box) or
            not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
        return None
    return [box[0] + (box[2] - box[0]) / 2, box[1] + (box[3] - box[1]) / 2]


def equipment_positions(sheet):
    """Fit nonuniform grid axes through exact source-symbol centers.

    Integer coordinates identify the ordered source axes. Every placed symbol is
    already at its grid intersection; neither the image nor its marks are moved.
    Nearby distinct centers remain distinct axes rather than being clustered.
    """
    requested = grid_settings(sheet.get("grid"))
    width, height = sheet.get("width"), sheet.get("height")
    components = sheet.get("components", [])
    if not isinstance(components, (list, tuple)):
        raise ValueError("Drawing components must be a list.")
    ids = Counter(item.get("id") for item in components if isinstance(item, Mapping)
                  and isinstance(item.get("id"), str) and item["id"].strip())
    positions, placed, warnings = [], [], []
    for index, original in enumerate(components):
        item = original if isinstance(original, Mapping) else {}
        component_id = item.get("id")
        anchor = _anchor(item.get("bbox"), width, height)
        record = {"component_id": component_id, "tag": item.get("tag"), "type": item.get("type"),
                  "coordinate": None, "display_point": None, "source_anchor": anchor}
        positions.append(record)
        if not isinstance(component_id, str) or not component_id.strip():
            warnings.append(f"Observation {index + 1} has no valid component ID and cannot be placed.")
        elif ids[component_id] > 1:
            message = f"Component ID {component_id} is repeated; its observations cannot be placed unambiguously."
            if message not in warnings:
                warnings.append(message)
        elif anchor is None:
            warnings.append(f"Component {component_id} has no valid symbol box inside the source image and remains unplaced.")
        else:
            placed.append(record)

    xs = sorted({record["source_anchor"][0] for record in placed})
    ys = sorted({record["source_anchor"][1] for record in placed})
    columns = {value: index + 1 for index, value in enumerate(xs)}
    rows = {value: index + 1 for index, value in enumerate(ys)}
    by_point = {}
    for record in placed:
        x, y = record["source_anchor"]
        record["coordinate"] = [columns[x], rows[y]]
        record["display_point"] = [x, y]
        by_point.setdefault((x, y), []).append(record)
    shared = [records for records in by_point.values() if len(records) > 1]
    for records in shared:
        column, row = records[0]["coordinate"]
        names = ", ".join(record["component_id"] for record in records)
        warnings.append(f"Components {names} share the same source center and grid intersection ({column},{row}).")

    return {"sheet_id": sheet["id"],
            "grid": {"columns": len(xs), "rows": len(ys), "origin": "top-left"},
            "requested_grid": requested,
            "layout_kind": "source-intersections", "axes": {"x": xs, "y": ys},
            "coordinates": positions,
            "display": {"width": width if _number(width) and width > 0 else 0,
                        "height": height if _number(height) and height > 0 else 0},
            "convention": "One-based indices of nonuniform grid axes fitted through exact source-symbol centers. "
                          "Columns increase right and rows downward. Axis spacing follows the source image; "
                          "coordinates do not measure distance.",
            "shared_cells": bool(shared), "warnings": warnings}
