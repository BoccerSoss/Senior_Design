"""Pixel evidence for tanks, vessels, valve bodies and pump housings on drawings."""
from __future__ import annotations

import math
import re

from PIL import Image, ImageFilter


GRID = 128
_STROKE = re.compile(b"\xff+")


def _valve_bodies(mask, *, surrounding=False, whole_sheet=False):
    """Find paired bars joined by a slanted member or two opposing triangles."""
    width, height = mask.size
    vertical = mask.transpose(Image.Transpose.TRANSPOSE).tobytes()
    bars = []
    # Group the neighboring pixels of each straight bar into a single feature.
    # A tapered triangle also has shorter interior columns; only its full-height
    # outer bar can supply the two endpoints of the matching diagonal.
    previous = []
    minimum_fraction = .00004 if whole_sheet else (.032 if surrounding else .24)
    short_axis = min(width, height)
    minimum = max(12, round(short_axis * .0045)) if whole_sheet else max(9, round(height * min(.23, minimum_fraction)))
    maximum = short_axis * .045 if whole_sheet else height
    for x in range(width):
        current = []
        for match in _STROKE.finditer(vertical, x * height, (x + 1) * height):
            y0, y1 = match.start() - x * height, match.end() - x * height - 1
            if not minimum <= y1 - y0 <= maximum:
                continue
            bar = next((b for b in previous if abs(b[2] - y0) <= 2 and abs(b[3] - y1) <= 2), None)
            if bar is None:
                bar = [x, x, y0, y1]
                bars.append(bar)
            else:
                bar[1] = x
            current.append(bar)
        previous = current

    close = mask.filter(ImageFilter.MaxFilter(3)).load()
    near = mask.filter(ImageFilter.MaxFilter(5)).load()
    pixels = mask.load()
    for i, left in enumerate(bars):
        for j in range(i + 1, len(bars)):
            right = bars[j]
            if right[0] - left[1] > 3.4 * (left[3] - left[2]):
                break
            x0, x1 = (left[0] + left[1]) / 2, (right[0] + right[1]) / 2
            h = min(left[3] - left[2], right[3] - right[2])
            w = x1 - x0
            if not (0.85 * h <= w <= 3.2 * h and w * h >= minimum_fraction * width * height):
                continue
            if max(abs(left[2] - right[2]), abs(left[3] - right[3])) > max(3, h * .23):
                continue
            if surrounding:
                # Reject two interior columns of one filled wedge. A true
                # outer bar has clear paper beyond both ends at this offset.
                outside = []
                offset = max(3, round(w * .07))
                for bar, x in ((left, left[0] - offset), (right, right[1] + offset)):
                    if 0 <= x < width:
                        outside += [bool(pixels[x, round(bar[2] + (bar[3] - bar[2]) * f)]) for f in (.13, .87)]
                if outside and sum(outside) > len(outside) * .3:
                    continue
            # Each sample follows the observed bar endpoints, allowing a
            # slightly skewed scan. The empty spaces above and below the
            # meeting point distinguish opposing wedges from a filled block.
            def samples(left_y, right_y):
                field = close if h < 25 else near
                return sum(bool(field[round(x0 + w * t / 10), round(left_y + (right_y - left_y) * t / 10)])
                           for t in range(1, 10))

            if max(samples(left[2], right[3]), samples(left[3], right[2])) < 8:
                continue
            empty = sum(not pixels[round(x0 + w * f), round((left[2] + right[2]) / 2 + h * v)]
                        for f in (.35, .45, .55, .65) for v in (.13, .87))
            # At the throat the shape narrows to a diagonal or two meeting
            # tips. An ink-filled logo or text block can contain incidental
            # aligned bars and diagonals but lacks this narrowing.
            if empty < 6:
                continue
            if w < 1.25 * h:
                top = round((left[2] + right[2]) / 2)
                slim = sum(sum(bool(pixels[round(x0 + w*f), y]) for y in range(top, top+h))
                           <= .46*h for f in (.36,.42,.58,.64))
                if slim < 3:
                    continue
            yield left[0], min(left[2], right[2]), right[1] + 1, max(left[3], right[3]) + 1


def _trim_line_ends(mask):
    """Trim protruding horizontal pipe strokes before testing a compact housing."""
    width, height = mask.size
    vertical = mask.transpose(Image.Transpose.TRANSPOSE).tobytes()
    occupied = []
    for x in range(width):
        column = vertical[x * height:(x + 1) * height]
        if column.rfind(b"\xff") - column.find(b"\xff") >= max(3, height * .18):
            occupied.append(x)
    if not occupied or occupied[-1] <= occupied[0]:
        return mask
    return mask.crop((occupied[0], 0, occupied[-1] + 1, height))


def _angle_valve(mask):
    """Two filled wedges with perpendicular bases terminating at a common tip."""
    w, h = mask.size
    if not .7 <= w / h <= 1.45:
        return False
    pixels = mask.load()
    def present(x, y):
        return bool(pixels[round((w - 1) * x), round((h - 1) * y)])

    inside = ((.13,.04),(.32,.05),(.51,.03),(.27,.25),(.37,.26),(.35,.40),
              (.53,.64),(.73,.56),(.78,.70),(.96,.46),(.96,.67),(.96,.88))
    outside = ((.82,.05),(.88,.20),(.11,.43),(.08,.60),(.19,.85),
               (.33,.95),(.52,.90),(.62,.28))
    return all(present(x,y) for x,y in inside) and all(not present(x,y) for x,y in outside)


def _pump_housing(mask):
    """Look for the scroll arc and its integral stepped, tangential discharge."""
    mask = _trim_line_ends(mask)
    if not .9 <= mask.width / mask.height <= 1.55:
        return False
    field = mask.resize((GRID, GRID), Image.Resampling.NEAREST)
    near = field.filter(ImageFilter.MaxFilter(13)).load()
    def hit(x, y):
        return bool(near[round(x * (GRID - 1)), round(y * (GRID - 1))])

    # Three quarters of the housing curve return from the bottom of the
    # discharge step around the bottom and left to the top tangent.
    arc = [(0.42 + 0.41 * math.cos(math.radians(a)), .50 + .49 * math.sin(math.radians(a)))
           for a in range(0, 271, 9)]
    discharge = [(x / 100, .025) for x in range(48, 99, 6)]
    discharge += [(.975, y / 100) for y in range(5, 48, 5)]
    discharge += [(x / 100, .50) for x in range(85, 99, 3)]
    return (sum(hit(*p) for p in arc) >= len(arc) * .86 and
            sum(hit(*p) for p in discharge) >= len(discharge) * .91 and
            not any(hit(x, y) for x, y in ((.52,.35), (.52,.65), (.96,.75))))


def _vessel_outline(mask):
    """Look for long straight sides separated by two rounded end caps."""
    if not 1.8 <= mask.height / mask.width <= 4:
        return False
    field = mask.resize((GRID, GRID), Image.Resampling.NEAREST)
    near = field.filter(ImageFilter.MaxFilter(9)).load()
    def has(x, y):
        return bool(near[round((GRID - 1) * x), round((GRID - 1) * y)])

    cap = mask.width / mask.height / 2
    upper = [(.50 + .49 * math.cos(math.radians(a)), cap - (cap - .01) * math.sin(math.radians(a)))
             for a in range(0, 181, 12)]
    outline = upper + [(x, 1-y) for x,y in upper]
    outline += [(x, y/10) for y in range(3, 8) for x in (.015, .985)]
    clear = ((.06,.03), (.94,.03), (.06,.97), (.94,.97), (.50,.20), (.50,.80))
    return sum(has(x, y) for x, y in outline) >= len(outline) * .88 and not any(has(x,y) for x,y in clear)


def _area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def _overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _line_corner(mask):
    """An open L consisting only of two straight strokes at adjacent box edges."""
    width, height = mask.size
    margin = max(3, round(min(width, height) * .12))
    if min(width, height) < 20:
        return False
    for edge in (True, False):
        for upper in (True, False):
            x0, x1 = (margin, width) if edge else (0, width - margin)
            y0, y1 = (margin, height) if upper else (0, height - margin)
            if not mask.crop((x0, y0, x1, y1)).getbbox():
                return True
    return False


def _nearby_valve(image, box):
    """Reconnect pieces of a valve split by the geometric pipe-suppression pass."""
    l,t,r,b = box
    pad = round(min(110, max(r-l,b-t) * 1.4))
    crop_box = (max(0,l-pad), max(0,t-pad), min(image.width,r+pad), min(image.height,b+pad))
    roi = image.crop(crop_box)
    small = roi.copy()
    small.thumbnail((200,200), Image.Resampling.LANCZOS)
    sx, sy = roi.width / small.width, roi.height / small.height
    mask = small.point(lambda v: 255 if v < 160 else 0)
    candidates = []
    for transposed in (False, True):
        view = mask.transpose(Image.Transpose.TRANSPOSE) if transposed else mask
        for rect in _valve_bodies(view, surrounding=True):
            x0,y0,x1,y1 = (rect[1], rect[0], rect[3], rect[2]) if transposed else rect
            body = [crop_box[0]+round(x0*sx), crop_box[1]+round(y0*sy),
                    crop_box[0]+round(x1*sx), crop_box[1]+round(y1*sy)]
            overlap = _overlap(box, body)
            if (overlap < .38 * min(_area(box), _area(body)) or
                    r-l > 2.15 * (body[2]-body[0]) or b-t > 2.3 * (body[3]-body[1])):
                continue
            candidates.append((overlap, _area(body), body))
    if not candidates:
        return None
    best = max(candidates)[2]
    if any(_overlap(best, body) < .45 * min(area, _area(best)) for _,area,body in candidates):
        return None
    return best


def _separate_inline_symbols(image, box, mask):
    """Split a wide region only where a length of pipe separates two recognizable glyphs."""
    l,t,r,b = box
    for transposed in (False, True):
        view = mask.transpose(Image.Transpose.TRANSPOSE) if transposed else mask
        width, height = view.size
        if width < 2.4 * height:
            continue
        raw = view.transpose(Image.Transpose.TRANSPOSE).tobytes()
        narrow = []
        for x in range(width):
            column = raw[x*height:(x+1)*height]
            narrow.append(column.rfind(b"\xff") - column.find(b"\xff") <= max(4, height * .17))
        for group in re.finditer(b"\x01+", bytes(narrow)):
            start, end = group.span()
            if end - start < max(16, height*.43) or min(start, width-end) < height * .52:
                continue
            regions = []
            for a,z in ((0,start),(end,width)):
                crop = (0,a,height,z) if transposed else (a,0,z,height)
                bounds = mask.crop(crop).getbbox()
                if bounds:
                    translated = [bounds[j]+(l+crop[0] if j%2==0 else t+crop[1]) for j in range(4)]
                    if classify_symbol(image, translated)["type"] != "unknown":
                        regions.append(translated)
            if len(regions) == 2:
                return regions
    return None


def refine_symbol_boxes(image, boxes, arrows):
    """Repair truncated valve bodies and keep identified arrowheads out of equipment."""
    heads = []
    for arrow in arrows:
        xs, ys = zip(arrow["arrow_tip"], *arrow["arrow_wings"])
        heads.append([min(xs), min(ys), max(xs)+1, max(ys)+1])
    records = []
    for box in boxes:
        if any(_overlap(box, head) > .62 * max(_area(box), _area(head)) for head in heads):
            continue
        mask = image.crop(box).point(lambda v: 255 if v < 160 else 0)
        if _line_corner(mask):
            continue
        separate = _separate_inline_symbols(image, box, mask)
        if separate:
            records.extend((part, None) for part in separate)
            continue
        # A complete outline needs no repair. Preserve its full original box,
        # including an operator or external connection entering the symbol.
        if classify_symbol(image, box)["type"] != "unknown":
            records.append((box, None))
            continue
        body = _nearby_valve(image, box)
        records.append((_union(box, body) if body else box, body))
    kept = []
    for box, body in sorted(records, key=lambda pair: (pair[1] is not None, pair[0])):
        if body:
            for i, (prior, prior_body) in enumerate(kept):
                target = prior_body or prior
                if _overlap(body, target) > .73 * min(_area(body), _area(target)):
                    kept[i] = (_union(prior, box), body)
                    break
            else:
                kept.append((box, body))
        else:
            kept.append((box, body))
    return [box for box,_ in kept]


def _longest_run(values):
    longest = current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def _near(ink, x, y, radius=3):
    x, y = round(x), round(y)
    return any(ink[yy * GRID + xx]
               for yy in range(max(0, y-radius), min(GRID, y+radius+1))
               for xx in range(max(0, x-radius), min(GRID, x+radius+1)))


def _fraction(points, ink, radius=3):
    points = list(points)
    return sum(_near(ink, x, y, radius) for x, y in points) / len(points) if points else 0.0


def classify_symbol(image, bbox):
    """Return ``{type,evidence,uncertainty}`` using only pixels within ``bbox``.

    Boxes use top-left original-image coordinates with exclusive maxima. The
    classifier never reads tags, filenames, graph records, references or source
    vector geometry. Unsupported, tiny or ambiguous shapes remain unknown.
    """
    if not isinstance(image, Image.Image):
        raise ValueError("classify_symbol requires a Pillow image, not a filename or reference record")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox):
        raise ValueError("Symbol box must contain four finite pixel coordinates")
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
        raise ValueError("Symbol box must enclose positive area within the image")

    def unknown(reason):
        return {"type": "unknown", "evidence": reason,
                "uncertainty": "No supported simplified-symbol rule matched reliably; retain for human review."}

    if x1-x0 < 24 or y1-y0 < 24:
        return unknown("Candidate is too small for reliable outline/stroke measurements.")
    crop = image.crop((math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))).convert("L")
    # Tighten small candidate margins using visible ink, without outside text.
    binary = crop.point(lambda value: 255 if value < 160 else 0)
    bounds = binary.getbbox()
    if bounds is None:
        return unknown("No dark outline pixels are present in the candidate box.")
    crop = crop.crop(bounds)
    aspect = crop.width / crop.height
    if crop.width < 18 or crop.height < 18:
        return unknown("Visible ink has unsupported size or aspect ratio for these educational rules.")
    # Use a bounded image with preserved aspect ratio for stroke measurements.
    small = crop.copy()
    small.thumbnail((160,160), Image.Resampling.LANCZOS)
    mask = small.point(lambda value: 255 if value < 160 else 0)
    if any(any(_valve_bodies(view)) for view in (mask, mask.transpose(Image.Transpose.TRANSPOSE))):
        return {"type": "valve",
                "evidence": "Raster has two aligned end bars joined by a diagonal member or opposed triangle bodies.",
                "uncertainty": "Generic valve symbol; the outline alone does not establish its subtype or operating state."}
    turned = mask
    for _ in range(4):
        if _angle_valve(turned):
            return {"type": "valve", "evidence": "Raster has two opposed filled wedges meeting at a right angle.",
                    "uncertainty": "Generic angle valve geometry; subtype and operating state are unverified."}
        turned = turned.transpose(Image.Transpose.ROTATE_90)
    if _pump_housing(mask) or _pump_housing(mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)):
        return {"type": "pump", "evidence": "Raster has a rounded scroll housing with a stepped tangential outlet.",
                "uncertainty": "Pump housing identified visually; model and operating state require other evidence."}
    if _vessel_outline(mask) or _vessel_outline(mask.transpose(Image.Transpose.TRANSPOSE)):
        return {"type": "vessel", "evidence": "Raster has a closed elongated body with parallel sides and two rounded end caps.",
                "uncertainty": "Vessel outline only; service and contents are unverified."}
    if not 0.4 <= aspect <= 3.0:
        return unknown("Visible ink has unsupported aspect ratio for these educational rules.")
    ink = crop.resize((GRID, GRID), Image.Resampling.NEAREST).point(lambda value: 1 if value < 160 else 0).tobytes()
    density = sum(ink) / (GRID * GRID)
    if density > 0.40:
        return unknown("Candidate is filled or too dense to establish the supported outline conventions.")

    left_side = max(_longest_run(ink[y*GRID+x] for y in range(GRID)) for x in range(0, 14)) / GRID
    right_side = max(_longest_run(ink[y*GRID+x] for y in range(GRID)) for x in range(GRID-14, GRID)) / GRID
    side_support = min(left_side, right_side)
    center_ys = [y for y in range(GRID) if any(ink[y*GRID+x] for x in range(60, 68))]
    center_span = (max(center_ys)-min(center_ys)+1) / GRID if center_ys else 0.0
    top_horizontal = max(_longest_run(ink[y*GRID:(y+1)*GRID]) for y in range(0, 52)) / GRID
    bottom_horizontal = max(_longest_run(ink[y*GRID:(y+1)*GRID]) for y in range(108, GRID)) / GRID
    diagonal = min(
        _fraction(((x, x) for x in range(12, 116, 3)), ink, radius=4),
        _fraction(((x, GRID-1-x) for x in range(12, 116, 3)), ink, radius=4),
    )

    if side_support >= 0.65 and center_span <= 0.35 and diagonal >= 0.80:
        return {"type": "valve",
                "evidence": "Raster has opposed triangle outlines, long outer sides and diagonals meeting near the center.",
                "uncertainty": "Simplified generic valve geometry only; subtype, actuation, position and flow direction are unspecified."}

    if side_support >= 0.55 and center_span >= 0.75 and top_horizontal >= 0.65 and bottom_horizontal >= 0.65:
        return {"type": "tank",
                "evidence": "Raster has long parallel outer sides and separated horizontal top/body and bottom outline strokes.",
                "uncertainty": "Simplified tank outline match; roof, contents, level and operating condition are not interpreted."}

    # A circle alone is ambiguous. Require both supported integral pump strokes.
    # The normalized outline tolerance accommodates line width and raster boxes.
    ring = _fraction(((63.5 + 62*math.cos(math.radians(a)),
                       63.5 + 62*math.sin(math.radians(a))) for a in range(0, 360, 8)), ink, radius=5)
    center_entry = _fraction(((x, 63.5) for x in range(16, 62, 3)), ink, radius=4)
    tangent_outlet = sum(any(ink[y*GRID+x] for y in range(0, 11)) for x in range(78, GRID)) / (GRID-78)
    if 0.75 <= aspect <= 1.30 and ring >= 0.82 and side_support < 0.55 and center_entry >= 0.85 and tangent_outlet >= 0.85:
        return {"type": "pump",
                "evidence": "Raster has a near-circular outline, a horizontal entry stroke reaching its center and a top tangent outlet stroke.",
                "uncertainty": "Simplified pump geometry match only; subtype, running state and flow direction are unspecified."}

    return unknown("Candidate lacks a complete supported tank, opposed-triangle valve or stroked circular-pump outline.")
