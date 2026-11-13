"""Trace horizontal pipe contacts and small flow arrows from raster ink."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re

from PIL import Image


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


_LONG_INK_RUN = re.compile(rb"\x01{36,}")
_MAX_HEAD_HALF_WIDTH = 30


def _cross_section(mask, width, height, x, y):
    """Count ink on both sides of an already-dark point on the shaft."""
    above = below = 0
    while above < min(_MAX_HEAD_HALF_WIDTH, y) and mask[(y - above - 1) * width + x]:
        above += 1
    while below < min(_MAX_HEAD_HALF_WIDTH, height - y - 1) and mask[(y + below + 1) * width + x]:
        below += 1
    return above, below


def _filled_heads(profile):
    """Locate filled heads in a cross-section profile, in shaft-to-tip order."""
    widths = [above + below + 1 for above, below in profile]
    for rear in range(8, len(widths) - 9):
        width = widths[rear]
        if width < 9 or width - widths[rear - 3] < 5:
            continue
        stem = max(widths[rear - 8:rear - 2])
        if stem > 10 or width < 2 * stem:
            continue
        base = max(range(rear, rear + 5), key=widths.__getitem__)
        widest = widths[base]
        above, below = profile[base]
        if widest > 53 or min(above, below) < 3 or abs(above - below) > max(3, widest * 0.30):
            continue
        narrow = max(stem + 2, widest * 0.42)
        for end in range(base + 1, min(len(widths), base + 100)):
            if widths[end] > widest + 3:
                break
            if widths[end] > narrow:
                continue
            length = end - base
            if not max(8, 0.42 * widest) <= length <= 5 * widest:
                break
            # Each side should narrow along the same axis. A flange, a crossing,
            # or a wide black rectangle can widen suddenly but has no taper.
            tolerance = max(2.5, widest * 0.16)
            start = profile[base]
            finish = profile[end]
            if any(a - b < 2 for a, b in zip(start, finish)):
                break
            matching = sum(
                all(abs(value - (a + (b - a) * step / length)) <= tolerance
                    for value, a, b in zip(section, start, finish))
                for step, section in enumerate(profile[base:end + 1])
            )
            if matching < 0.85 * (length + 1):
                break
            tip = min(len(widths) - 1, round(base + length * (widest - 1) / (widest - widths[end])))
            # A terminal arrow may meet a junction. The next perpendicular ink
            # bounds the taper instead of extending the estimated tip past it.
            for position in range(end + 1, tip + 1):
                if widths[position] > widest + 3:
                    tip = position
                    break
            if len(widths) - 1 - tip <= 6 and widths[-1] <= narrow:
                tip = len(widths) - 1
            if tip - base < 0.85 * widest:
                break
            yield tip, base, above, below
            break


def _axial_arrows(mask, width, height):
    """Yield points in the scan's coordinate space for both axial directions."""
    for y in range(1, height - 1):
        offset = y * width
        for run in _LONG_INK_RUN.finditer(mask, offset, offset + width):
            start, end = run.start() - offset, run.end() - offset
            # A head must have ink away from the thin stem on both sides.
            # The byte slice check avoids scanning the long lines of a frame.
            if 1 not in mask[max(0, y - 4) * width + start:max(0, y - 4) * width + end]:
                continue
            if 1 not in mask[min(height - 1, y + 4) * width + start:min(height - 1, y + 4) * width + end]:
                continue
            profile = [_cross_section(mask, width, height, x, y) for x in range(start, end)]
            for sign, sections in ((1, profile), (-1, profile[::-1])):
                for tip, base, above, below in _filled_heads(sections):
                    origin = start if sign > 0 else end - 1
                    wing_x = origin + sign * base
                    center_error = abs(above - below)
                    yield sign, (origin + sign * tip, y), ((wing_x, y - above), (wing_x, y + below)), center_error


def _outward_pairs(mask, width, heads):
    """Reject opposed arrowheads that share one axis and point away from each other."""
    rejected = set()
    for first, (sign, tip, wings) in enumerate(heads):
        if sign != -1:
            continue
        for second, (other, end, other_wings) in enumerate(heads):
            if other != 1 or tip[0] >= end[0] or abs(tip[1] - end[1]) > 3:
                continue
            start_x, end_x = wings[0][0], other_wings[0][0]
            if start_x >= end_x:
                continue
            top = max(0, min(tip[1], end[1]) - 1)
            bottom = min(len(mask) // width, max(tip[1], end[1]) + 2)
            if horizontal_stroke(mask, width, start_x, end_x, top, bottom) is not None:
                rejected.update((first, second))
    return rejected


def _scan_heads(mask, width, height):
    observations = sorted(_axial_arrows(mask, width, height), key=lambda mark: mark[-1])
    selected = []
    for sign, tip, wings, _ in observations:
        if any(other == sign and abs(x - tip[0]) <= 9 and abs(y - tip[1]) <= 4
               for other, (x, y), _ in selected):
            continue
        selected.append((sign, tip, wings))

    rejected = _outward_pairs(mask, width, selected)
    for index, (sign, tip, wings) in enumerate(selected):
        if index in rejected:
            continue
        if any(other == -sign and abs(x - tip[0]) <= 12 and abs(y - tip[1]) <= 12
               for other, (x, y), _ in selected):
            continue
        yield sign, tip, wings


def _arrow_observation(direction, tip, wings):
    return {"direction": direction, "arrow_tip": list(tip), "arrow_wings": [list(wing) for wing in wings]}


def _diagonal_heads(image, slope, negative, positive):
    width, height = image.size
    # Keep the shear padding proportional on very wide scans. Overlap covers
    # the shaft and head near each boundary; each tip belongs to one tile.
    span, overlap = max(1024, 3 * height), 256
    for start in range(0, width, span):
        end = min(start + span, width)
        left, right = max(0, start - overlap), min(width, end + overlap)
        tile = image if left == 0 and right == width else image.crop((left, 0, right, height))
        for sign, tip, wings in _sheared_heads(tile, slope):
            if start <= tip[0] + left < end:
                yield _arrow_observation(negative if sign < 0 else positive,
                                         (tip[0] + left, tip[1]),
                                         tuple((x + left, y) for x, y in wings))


def _sheared_heads(image, slope):
    # Shear a shallow diagonal shaft into the horizontal scan space. Use nearest
    # neighbor so the transform does not introduce intermediate ink values.
    width, height = image.size
    padding = (width + 2) // 3
    offset = padding if slope > 0 else 0
    scan = image.transform((width, height + padding), Image.Transform.AFFINE,
                           (1, 0, 0, slope, 1, -offset), Image.Resampling.NEAREST)

    def image_point(point):
        x, y = point
        return x, y + round(slope * x) - offset

    for sign, tip, wings in _scan_heads(scan.tobytes(), *scan.size):
        tip = image_point(tip)
        wings = tuple(image_point(wing) for wing in wings)
        if all(0 <= x < width and 0 <= y < height for x, y in (tip, *wings)):
            yield sign, tip, wings


def observe_flow_arrows(mask, width):
    """Find visible tapered arrowheads on axial and shallow diagonal shafts.

    Terminal heads can end freely or meet a junction. Direction is in image
    space, so this observation does not establish equipment endpoints.
    """
    height = len(mask) // width
    image = Image.frombytes("L", (width, height), mask)
    transpose = image.transpose(Image.Transpose.TRANSPOSE)
    observations = []
    for sign, tip, wings in _scan_heads(mask, width, height):
        observations.append(_arrow_observation("left" if sign < 0 else "right", tip, wings))
    for sign, tip, wings in _scan_heads(transpose.tobytes(), height, width):
        observations.append(_arrow_observation("up" if sign < 0 else "down", tip[::-1],
                                               tuple(wing[::-1] for wing in wings)))
    observations.extend(_diagonal_heads(image, 1 / 3, "up-left", "down-right"))
    observations.extend(_diagonal_heads(image, -1 / 3, "down-left", "up-right"))
    return sorted(observations, key=lambda mark: (mark["arrow_tip"][1], mark["arrow_tip"][0]))
