"""Trace horizontal pipe contacts and small flow arrows from raster ink."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


MAX_WING_DEPTH = 40


@dataclass(frozen=True)
class HorizontalStroke:
    start: int
    end: int
    top: int
    bottom: int

    @property
    def center(self):
        return (self.top + self.bottom) // 2

    @property
    def path(self):
        return [[self.start, self.center], [self.end, self.center]]


def horizontal_stroke(mask, width, start, end, top, bottom):
    """Find the first band of rows dark along the entire inclusive x interval."""
    first = None
    for y in range(top, bottom):
        row_offset = y * width
        if 0 not in mask[row_offset + start:row_offset + end + 1]:
            if first is None:
                first = y
        elif first is not None:
            return HorizontalStroke(start, end, first, y - 1)
    return HorizontalStroke(start, end, first, bottom - 1) if first is not None else None


@dataclass(frozen=True)
class ArrowWing:
    sign: int
    depth: int
    tip_x: float
    end: tuple[int, int]


def _connected_groups(points):
    """Yield 8-connected groups from a small strip outside the pipe stem."""
    while points:
        seed = points.pop()
        queue = deque([seed])
        group = []
        while queue:
            x, y = queue.popleft()
            group.append((x, y))
            for nx in range(x - 1, x + 2):
                for ny in range(y - 1, y + 2):
                    point = nx, ny
                    if point in points:
                        points.remove(point)
                        queue.append(point)
        yield group


def _wing(group, near_y, vertical_sign, stroke_width, center_y, reach):
    rows = {}
    for x, y in group:
        rows.setdefault((y - near_y) * vertical_sign + 1, []).append(x)
    depth = max(rows)
    if min(rows) != 1 or not max(4, stroke_width) <= depth < reach:
        return None
    widths = [len(rows[i]) for i in range(1, depth + 1)]
    filled = (widths[0] >= 2 * stroke_width
              and widths[depth // 2] <= widths[0] * 0.8
              and widths[-1] <= widths[0] / 2)
    centers = [(min(rows[i]) + max(rows[i])) / 2 for i in range(1, depth + 1)]
    slope = (centers[-1] - centers[0]) / (depth - 1)
    slope_limit = 4.0 if filled else 2.5
    if not 0.30 <= abs(slope) <= slope_limit or abs(centers[-1] - centers[0]) < 4:
        return None
    tolerance = max(2.5, stroke_width / 2, abs(slope) * depth / 5)
    if any(abs(center - (centers[0] + slope * i)) > tolerance for i, center in enumerate(centers)):
        return None

    # An open wing is narrow, while a filled triangle narrows toward its rear
    # corner. In either case the forward edge must taper back from the stem.
    sign = -1 if slope > 0 else 1
    forward = [max(rows[i], key=lambda x: x * sign) for i in range(1, depth + 1)]
    edge_slope = (forward[-1] - forward[0]) / (depth - 1)
    edge_limit = 8.0 if filled else 3.0
    if not 0.30 <= -edge_slope * sign <= edge_limit:
        return None
    if any(abs(x - (forward[0] + edge_slope * i)) > tolerance for i, x in enumerate(forward)):
        return None
    if max(widths) > abs(edge_slope) * depth + 2 * stroke_width + 2:
        return None
    offset = abs(near_y - center_y)
    tip_x = forward[0] - edge_slope * offset if filled else centers[0] - slope * offset
    end_x = min(rows[depth], key=lambda x: (abs(x - centers[-1]), x))
    end_y = near_y + (depth - 1) * vertical_sign
    return ArrowWing(sign, depth, tip_x, (end_x, end_y))


def _wings(mask, width, stroke, vertical_sign):
    height = len(mask) // width
    stroke_width = stroke.bottom - stroke.top + 1
    near_y = stroke.top - 1 if vertical_sign < 0 else stroke.bottom + 1
    # Leave the equipment contacts outside the search window. Reaching a crop
    # edge would give incomplete evidence of a wing.
    margin = max(3, stroke_width)
    xs = range(stroke.start + margin, stroke.end - margin + 1)
    ys = [near_y + i * vertical_sign for i in range(MAX_WING_DEPTH)
          if 0 <= near_y + i * vertical_sign < height]
    if not xs or len(ys) < 5:
        return []
    points = {(x, y) for y in ys for x in xs if mask[y * width + x]}
    wings = []
    for group in _connected_groups(points):
        if any(x in (xs[0], xs[-1]) for x, _ in group):
            continue
        wing = _wing(group, near_y, vertical_sign, stroke_width, stroke.center, len(ys))
        if wing is not None:
            wings.append(wing)
    return wings


def flow_direction(mask, width, stroke):
    """Record arrow landmarks only for paired, consistent wings on this pipe."""
    unknown = {"status": "unknown", "arrow_tip": None, "arrow_wings": None}
    if stroke.end - stroke.start < 20:
        return unknown
    candidates = []
    upper_wings = _wings(mask, width, stroke, -1)
    lower_wings = _wings(mask, width, stroke, 1)
    for upper in upper_wings:
        for lower in lower_wings:
            if upper.sign != lower.sign:
                continue
            max_depth = max(upper.depth, lower.depth)
            if abs(upper.depth - lower.depth) > max(3, max_depth / 3):
                continue
            if abs(upper.end[0] - lower.end[0]) > max(4, max_depth / 3):
                continue
            tip_difference = abs(upper.tip_x - lower.tip_x)
            tip_x = round((upper.tip_x + lower.tip_x) / 2)
            if tip_difference > max(4, max_depth / 3) or not stroke.start < tip_x < stroke.end:
                continue
            if any(upper.sign * (tip_x - wing.end[0]) <= 0 for wing in (upper, lower)):
                continue
            candidates.append((upper.sign, tip_difference, tip_x, upper.end, lower.end))

    if len({item[0] for item in candidates}) != 1:
        return unknown
    sign, _, tip_x, upper, lower = min(candidates)
    return {
        "status": "source_to_target" if sign > 0 else "target_to_source",
        "arrow_tip": [tip_x, stroke.center],
        "arrow_wings": [list(upper), list(lower)],
    }


def is_pipe_arrow_box(mask, width, box):
    """Keep a pipe arrow out of equipment candidates when its whole box fits."""
    x0, y0, x1, y1 = box
    if x1 - x0 > MAX_WING_DEPTH * 3 or y1 - y0 > MAX_WING_DEPTH * 2:
        return False
    margin = 10
    if x0 < margin or x1 + margin > width:
        return False
    stroke = horizontal_stroke(mask, width, x0 - margin, x1 + margin - 1, y0, y1)
    if stroke is None:
        return False
    direction = flow_direction(mask, width, stroke)
    if direction["arrow_tip"] is None:
        return False
    points = [direction["arrow_tip"], *direction["arrow_wings"]]
    xs, ys = zip(*points)
    tolerance = max(5, stroke.bottom - stroke.top + 1)
    return all(abs(observed - arrow) <= tolerance for observed, arrow in
               zip(box, (min(xs), min(ys), max(xs) + 1, max(ys) + 1)))
