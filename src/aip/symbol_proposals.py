"""Fit complete equipment boxes to linework on a raster sheet before reading its labels."""
from __future__ import annotations

from PIL import Image, ImageFilter

from .baseline import connected_ink_spans
from .classification import _area, _overlap, _valve_bodies, classify_symbol, refine_symbol_boxes


def _opposed_contacts(mask, box):
    """Require a shaft on each end of the valve near the same centerline."""
    x, y, r, b = box
    span, depth = r - x, b - y
    reach = max(8, min(27, round(span * .22)))
    if x < reach or r + reach > mask.width:
        return False
    center = (y + b) // 2
    radius = max(3, round(.20 * depth))
    top, bottom = max(0, center - radius), min(mask.height, center + radius + 1)

    def rows(left):
        raw = mask.crop((left, top, left + reach, bottom)).tobytes()
        return [i for i in range(bottom - top)
                if raw.count(255, i * reach, (i + 1) * reach) >= .82 * reach]

    left_rows, right_rows = rows(x - reach), rows(r)
    tolerance = max(3, round(.07 * depth))
    return any(abs(a - b) <= tolerance for a in left_rows for b in right_rows)


def _include_operator(mask, body):
    """Extend a valve shoulder to a connected actuator on either side of its center."""
    x, y, r, b = body
    length = r - x
    left, right = max(0, int(x + .13 * length)), min(mask.width, int(r - .13 * length))
    top, bottom = max(0, int(y - .84 * length)), min(mask.height, int(b + .84 * length))
    crop = mask.crop((left, top, right, bottom))
    # Most ink inside the body belongs to its connected outline. Printed text
    # near an operator is a separate region and must not enlarge the box.
    groups = connected_ink_spans(crop.tobytes(), *crop.size)
    connected = max((spans for spans, _ in groups),
                    key=lambda spans: sum(hi - lo for row, lo, hi in spans if y <= row + top < b), default=[])
    for above in (True, False):
        branch = [(row + top, lo + left, hi + left) for row, lo, hi in connected
                  if (row + top < y if above else row + top >= b)]
        if not branch:
            continue
        end = min(row for row, _, _ in branch) if above else max(row for row, _, _ in branch) + 1
        extent = y - end if above else end - b
        xmin = min(lo for _, lo, _ in branch)
        xmax = max(hi for _, _, hi in branch)
        if (top + 1 < end < bottom - 1 and .10 * length < extent < .85 * length and
                xmax - xmin >= .20 * length and abs((xmin + xmax - x - r) / 2) <= .19 * length):
            if above:
                y = end
            else:
                b = end
    pad = max(1, round(.025 * length))
    return [max(0, x - pad), max(0, y - pad), min(mask.width, r + pad), min(mask.height, b + pad)]


def _valves(fields):
    found = []
    for vertical, view in enumerate(fields):
        for candidate in _valve_bodies(view, surrounding=True, whole_sheet=True):
            if not _opposed_contacts(view, candidate) or not _straight_member(view, candidate):
                continue
            x, y, r, b = _include_operator(view, candidate)
            box = [y, x, b, r] if vertical else [x, y, r, b]
            if not any(_overlap(box, old) > .65 * min(_area(box), _area(old)) for old in found):
                found.append(box)
    return found


def _straight_member(mask, box):
    """Distinguish an actual slanted valve stroke from coincident segments of curved housings."""
    crop = mask.crop(box)
    width, height = crop.size
    # A pivot dot, thick bar or noisy border can shift a corner relative to
    # the visible diagonal. Check straight lines in a narrow band at each end.
    field = crop.filter(ImageFilter.MaxFilter(3)).load()
    fractions = [.12 + i * .76 / 14 for i in range(15)]
    shifts = [round(height * f) for f in (0, -.06, .06, -.12, .12)]
    for start, end in ((0, height-1), (height-1, 0)):
        for first in shifts:
            for last in shifts:
                misses = 0
                for t in fractions:
                    x = round((width-1) * t)
                    y = round((start+first) * (1-t) + (end+last) * t)
                    if not (0 <= y < height and field[x, y]):
                        misses += 1
                        if misses > 2:
                            break
                else:
                    return True
    return False


def _housings(gray, mask):
    """Find closed white interiors, then check the ink surrounding each cavity."""
    width, height = mask.size
    short_axis = min(width, height)
    white = mask.point(lambda value: 0 if value else 1)
    for spans, (x, y, r, b) in connected_ink_spans(white.tobytes(), width, height):
        w, h = r - x, b - y
        if min(w, h) < .007 * short_axis or max(w, h) > .18 * short_axis:
            continue
        if x == 0 or y == 0 or r == width or b == height:
            continue
        area = sum(hi - lo for _, lo, hi in spans)
        if not .62 <= area / (w * h) <= .98:
            continue
        for fraction in (.07, .11, .035):
            pad = max(2, round(min(w, h) * fraction))
            box = [max(0, x - pad), max(0, y - pad), min(width, r + pad), min(height, b + pad)]
            if classify_symbol(gray, box)["type"] in ("pump", "vessel"):
                yield box
                break


def _fit_local_valve(gray, fields, box):
    """Trim a captured pipe stub or recover an actuator cut by stroke suppression."""
    x, y, r, b = box
    kind = classify_symbol(gray, box)["type"]
    if kind not in ("valve", "unknown"):
        return box
    mask = fields[0]
    best = None
    for pad in (0, round(.65 * min(r-x, b-y))):
        left, top = max(0, x - pad), max(0, y - pad)
        window = mask.crop((left, top, min(mask.width, r + pad), min(mask.height, b + pad)))
        for vertical in (False, True):
            view = window.transpose(Image.Transpose.TRANSPOSE) if vertical else window
            for candidate in _valve_bodies(view):
                x0, y0, x1, y1 = candidate
                if vertical:
                    body = [y0 + left, x0 + top, y1 + left, x1 + top]
                else:
                    body = [x0 + left, y0 + top, x1 + left, y1 + top]
                oriented = [body[1], body[0], body[3], body[2]] if vertical else body
                field = fields[int(vertical)]
                contacts = _opposed_contacts(field, oriented)
                if not contacts and (kind != "valve" or pad):
                    continue
                fitted = _include_operator(field, oriented)
                if vertical:
                    fitted = [fitted[1], fitted[0], fitted[3], fitted[2]]
                shared = _overlap(box, fitted)
                small, large = sorted((_area(box), _area(fitted)))
                if shared < .48 * small or small < .12 * large:
                    continue
                rank = (contacts, _area(fitted), shared, fitted)
                if best is None or rank > best:
                    best = rank
    if best:
        _, area, shared, fitted = best
        if shared < .80 * (_area(box) + area - shared):
            return fitted
    return box


def recover_symbol_boxes(gray, boxes, arrows):
    """Combine local candidates with complete valve and enclosed housing observations."""
    local = refine_symbol_boxes(gray, boxes, arrows)
    mask = gray.point(lambda value: 255 if value < 160 else 0)
    fields = (mask, mask.transpose(Image.Transpose.TRANSPOSE))
    complete = _valves(fields) + list(_housings(gray, mask))
    retained = []
    for box in local:
        for shape in complete:
            shared = _overlap(box, shape)
            small, large = sorted((_area(box), _area(shape)))
            if shared >= .46 * small and shared >= .14 * (small + large - shared):
                break
        else:
            retained.append(_fit_local_valve(gray, fields, box))
    return complete + retained
