"""Trace connected ink upstream of visible horizontal arrows."""
from __future__ import annotations

from collections import deque
from math import ceil, floor


def _ink(mask, width, x, y):
    return 0 <= x < width and 0 <= y < len(mask) // width and bool(mask[y * width + x])


def _trace_row(mask, width, tip, step):
    """Choose a nearby row attached to the tip with the longest upstream stem."""
    x, y = tip
    options = []
    for row in range(y - 2, y + 3):
        if not all(_ink(mask, width, x, j) for j in range(min(row, y), max(row, y) + 1)):
            continue
        length = 0
        while _ink(mask, width, x + (length + 1) * step, row):
            length += 1
        options.append((-length, abs(row - y), row))
    return min(options)[2] if options else None


def _eligible_boxes(components, row):
    """Keep localized component candidates rather than sheet borders or tall trunks."""
    for component in components:
        if component.get("bbox") is None:
            continue
        x0, y0, x1, y1 = component["bbox"]
        box = floor(x0), floor(y0), ceil(x1), ceil(y1)
        left, top, right, bottom = box
        w, h = right - left, bottom - top
        if top <= row < bottom and w >= 15 and h >= 15 and 0.4 <= w / h <= 4 and w * h <= 60_000:
            yield component["id"], box


def _crosses_branch(mask, width, x, row):
    """Stop when a perpendicular ink arm extends outside the pipe stroke."""
    return any(all(_ink(mask, width, x, row + direction * offset) for offset in range(1, 11))
               for direction in (-1, 1))


def _walk_box(mask, width, box, entry, step):
    """Follow actual ink from a component port to its opposite on-axis port."""
    left, top, right, bottom = box
    row = entry[1]
    exit_x = left if step < 0 else right - 1
    exit_point = (exit_x, row)
    can_exit = _ink(mask, width, exit_x + step, row)
    queue = deque([entry])
    visited = {entry: None}
    terminal = None
    while queue:
        point = queue.popleft()
        x, y = point
        if terminal is None and (x - entry[0]) * step >= 4:
            terminal = point
        if can_exit and point == exit_point:
            terminal = point
            break
        for dx in (step, 0, -step):
            for dy in (0, -1, 1):
                next_point = x + dx, y + dy
                nx, ny = next_point
                if (left <= nx < right and top <= ny < bottom and next_point not in visited
                        and _ink(mask, width, nx, ny)):
                    visited[next_point] = point
                    queue.append(next_point)
    if terminal is None:
        return [], False
    through = can_exit and terminal == exit_point
    path = []
    while terminal != entry:
        path.append(terminal)
        terminal = visited[terminal]
    return path[::-1], through


def _polyline(points):
    """Collapse consecutive collinear raster steps without bridging any white pixels."""
    simplified = [points[0]]
    previous_step = None
    for a, b in zip(points, points[1:]):
        step = b[0] - a[0], b[1] - a[1]
        if step != previous_step and simplified[-1] != a:
            simplified.append(a)
        previous_step = step
    simplified.append(points[-1])
    return [list(point) for point in simplified]


def _route(mask, width, arrow, components):
    heading = arrow["direction"]
    if heading not in {"left", "right"}:
        return None
    step = -1 if heading == "right" else 1
    tip = tuple(arrow["arrow_tip"])
    x, y = tip
    row = _trace_row(mask, width, tip, step)
    if row is None:
        return None

    entries = {}
    for component_id, box in _eligible_boxes(components, row):
        left, _, right, _ = box
        port = right - 1 if step < 0 else left
        entries.setdefault(port, []).append((component_id, box))

    head_depth = max(abs(wing[0] - tip[0]) for wing in arrow["arrow_wings"])
    points = [tip]
    vertical_step = 1 if row > y else -1
    points.extend((x, j) for j in range(y + vertical_step, row + vertical_step, vertical_step))
    downstream_to_upstream = []
    while 0 <= x < width:
        ports = entries.get(x, [])
        if len(ports) > 1:
            break
        if ports:
            component_id, box = ports[0]
            segment, through = _walk_box(mask, width, box, (x, row), step)
            if not segment:
                break
            points.extend(segment)
            downstream_to_upstream.append(component_id)
            x = segment[-1][0]
            if not through:
                break

        next_x = x + step
        if not _ink(mask, width, next_x, row):
            break
        if abs(next_x - tip[0]) > head_depth + 3 and next_x not in entries and _crosses_branch(mask, width, next_x, row):
            break
        x = next_x
        points.append((x, row))
    if not downstream_to_upstream:
        return None
    return {"arrow_id": arrow["id"], "path": _polyline(points[::-1]),
            "component_ids": downstream_to_upstream[::-1]}


def trace_arrow_routes(mask, width, arrows, components):
    """Report visible upstream routes that reach or cross a component port."""
    routes = []
    for arrow in arrows:
        route = _route(mask, width, arrow, components)
        if route is not None:
            routes.append(route)
    return routes
