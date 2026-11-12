"""Assign one-based drawing-grid coordinates to equipment."""


def _center(box):
    x0, y0, x1, y1 = box
    return (x0 + x1) / 2, (y0 + y1) / 2


def equipment_grid(equipment, flow_routes):
    """Place pump-led routes first, then group remaining symbols by height."""
    by_id = {item["id"]: item for item in equipment}
    boxes = {item_id: item["bbox"] for item_id, item in by_id.items() if item.get("bbox") is not None}
    centers = {item_id: _center(box) for item_id, box in boxes.items()}
    coordinates = {}
    row_number = 1

    def visual_order(item_id):
        x, y = centers[item_id]
        return y, x, item_id

    pump_routes = []
    for route in flow_routes:
        items = tuple(item_id for item_id in route["component_ids"] if item_id in boxes)
        if len(items) > 1 and items[0] == route["component_ids"][0] and by_id[items[0]]["type"] == "pump":
            pump_routes.append(items)

    # Each starting pump anchors one row. When two routes share a pump,
    # prefer the one with the most equipment, then use a stable ID tie-break.
    pump_routes.sort(key=lambda items: (visual_order(items[0]), -len(items), items))
    pump_starts = {items[0] for items in pump_routes}
    for items in pump_routes:
        pump = items[0]
        if pump in coordinates:
            continue
        row = [pump]
        row.extend(item_id for item_id in items[1:] if item_id not in coordinates and item_id not in pump_starts)
        for column, item_id in enumerate(row, 1):
            coordinates[item_id] = [column, row_number]
        row_number += 1

    bands = []
    for item_id in sorted(boxes.keys() - coordinates.keys(), key=visual_order):
        y = centers[item_id][1]
        height = boxes[item_id][3] - boxes[item_id][1]
        if bands:
            anchor = bands[-1][0]
            anchor_y = centers[anchor][1]
            anchor_height = boxes[anchor][3] - boxes[anchor][1]
            if y - anchor_y <= min(height, anchor_height) / 2:
                bands[-1].append(item_id)
                continue
        bands.append([item_id])

    for band in bands:
        band.sort(key=lambda item_id: (*centers[item_id], item_id))
        for column, item_id in enumerate(band, 1):
            coordinates[item_id] = [column, row_number]
        row_number += 1

    for column, item_id in enumerate(sorted(by_id.keys() - boxes.keys()), 1):
        coordinates[item_id] = [column, row_number]
    return coordinates
