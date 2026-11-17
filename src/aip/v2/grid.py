"""Regular and equipment-aligned coordinate grids on the original drawing."""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping
from fractions import Fraction
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


def _regular_axes(width, height, requested):
    if not (_number(width) and _number(height) and width > 0 and height > 0):
        return [], []
    # Exact ratios keep a requested line from being lost to rounding in floor division.
    width, height = Fraction(width), Fraction(height)
    step = max(width / requested["columns"], height / requested["rows"])

    def centers(span):
        count = max(1, span // step)
        first = (span - (count - 1) * step) / 2
        marks = [first + i * step for i in range(count)]
        rendered = dict.fromkeys(map(float, marks))
        # For subnormal dimensions two distinct lines may have the same JSON number.
        return marks if len(rendered) == count else list(map(Fraction, rendered))

    return centers(width), centers(height)


def _nearest_axis(axis, position):
    """Return a one-based index, choosing the higher index on a midpoint tie."""
    right = bisect_left(axis, position)
    if right == len(axis):
        return right
    if right and axis[right] != position and 2 * Fraction(position) < axis[right - 1] + axis[right]:
        return right
    return right + 1


def equipment_positions(sheet, layout="source"):
    """Place equipment on a regular lattice or its exact center-aligned axes."""
    if layout not in ("regular", "source"):
        raise ValueError("Choose the regular or source grid layout.")
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

    if layout == "regular":
        xs, ys = _regular_axes(width, height, requested)
        convention = ("One-based coordinates of the nearest regular grid intersection to each equipment center. "
                      "Columns increase right and rows downward. Midpoint ties go right or down.")
    else:
        xs = sorted({record["source_anchor"][0] for record in placed})
        ys = sorted({record["source_anchor"][1] for record in placed})
        convention = ("One-based indices of nonuniform grid axes fitted through exact source-symbol centers. "
                      "Columns increase right and rows downward. Axis spacing follows the source image; "
                      "coordinates do not measure distance.")
    by_point = {}
    for record in placed:
        x, y = record["source_anchor"]
        column, row = _nearest_axis(xs, x), _nearest_axis(ys, y)
        record["coordinate"] = [column, row]
        record["display_point"] = [float(xs[column - 1]), float(ys[row - 1])]
        by_point.setdefault((column, row), []).append(record)
    shared = [records for records in by_point.values() if len(records) > 1]
    if layout == "source":
        for records in shared:
            column, row = records[0]["coordinate"]
            names = ", ".join(record["component_id"] for record in records)
            warnings.append(f"Components {names} share the same source center and grid intersection ({column},{row}).")

    return {"sheet_id": sheet["id"],
            "grid": {"columns": len(xs), "rows": len(ys), "origin": "top-left"},
            "requested_grid": requested,
            "layout_kind": layout + "-intersections", "axes": {"x": list(map(float, xs)), "y": list(map(float, ys))},
            "coordinates": positions,
            "display": {"width": width if _number(width) and width > 0 else 0,
                        "height": height if _number(height) and height > 0 else 0},
            "convention": convention,
            "shared_cells": bool(shared), "warnings": warnings}
