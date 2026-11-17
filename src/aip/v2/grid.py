"""A drawing grid independent of piping topology and equipment sequencing."""
from __future__ import annotations

from math import floor


def grid_settings(value=None):
    value = value or {}
    result = {}
    for field, default in (("columns", 12), ("rows", 16)):
        count = value.get(field, default)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 200:
            raise ValueError("Grid rows and columns must be whole numbers from 1 to 200.")
        result[field] = count
    result["origin"] = "top-left"
    return result


def equipment_positions(sheet):
    grid = grid_settings(sheet.get("grid"))
    width, height = sheet["width"], sheet["height"]
    positions = []
    for item in sheet.get("components", []):
        box = item.get("bbox")
        if box is None:
            positions.append({"component_id": item["id"], "tag": item.get("tag"), "coordinate": None})
            continue
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        col = min(grid["columns"], max(1, floor(x / width * grid["columns"]) + 1))
        row = min(grid["rows"], max(1, floor(y / height * grid["rows"]) + 1))
        positions.append({"component_id": item["id"], "tag": item.get("tag"), "type": item.get("type"),
                          "coordinate": [col, row]})
    return {"sheet_id": sheet["id"], "grid": grid, "coordinates": positions,
            "convention": "One-based drawing cells from the top left. Equipment uses its symbol center.",
            "shared_cells": True}
