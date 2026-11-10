"""Conservative raster rules for a small set of simplified educational symbols.

The input is an image-derived candidate box, not a tag or an expected equipment
identity. These rules support outline tanks, opposed-triangle valves and circular
pumps with a center-entry stroke and a top tangent outlet. They are development
rules for those shapes, not general P&ID recognition or a standards claim.
"""
from __future__ import annotations

import math

from PIL import Image


GRID = 128


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
    if crop.width < 18 or crop.height < 18 or not 0.4 <= aspect <= 3.0:
        return unknown("Visible ink has unsupported size or aspect ratio for these educational rules.")
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
