"""Image-only prediction of direct equipment connections for the workbench and benchmark."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from itertools import combinations, groupby, product
from math import ceil, hypot
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageChops

from ..classical import extract_image
from ..v2.providers import MAX_IMAGE_BYTES, ProviderError, extract_provider
from .direction import arrow_direction, assign_directions, raster_arrows
from .line_model import CLASSES, CONTINUOUS, EXAMPLES_PER_CLASS, MODEL_ID, TRAINING_SEED, classify_stroke


def _segment_hits_box(a, b, box):
    """Clip a segment to an open rectangle; a line along the outside is allowed."""
    lo, hi = 0.0, 1.0
    for start, end, left, right in zip(a, b, box[:2], box[2:]):
        if start == end:
            if not left < start < right:
                return False
            continue
        first, last = sorted(((left - start) / (end - start), (right - start) / (end - start)))
        lo, hi = max(lo, first), min(hi, last)
        if lo >= hi:
            return False
    return True


def _blocked(path, components, source, target):
    return any(_segment_hits_box(a, b, item["bbox"])
               for item in components if item["id"] not in {source, target}
               for a, b in zip(path, path[1:]))


def _samples(a, b, pixels, width, height):
    dx, dy = b[0] - a[0], b[1] - a[1]
    steps = max(1, ceil(max(abs(dx), abs(dy))))
    return [pixels[min(width - 1, max(0, round(a[0] + dx * i / steps))),
                   min(height - 1, max(0, round(a[1] + dy * i / steps)))] != 0
            for i in range(steps + 1)]


def _ports(box, ink):
    """Find groups of dark strokes entering each symbol face from outside."""
    width, height = ink.size
    pixels = ink.load()
    x0, y0, x1, y1 = map(round, box)
    ports = []
    for (dx, dy), fixed, low, high in [
        ((-1, 0), x0, y0, y1), ((1, 0), x1 - 1, y0, y1),
        ((0, -1), y0, x0, x1), ((0, 1), y1 - 1, x0, x1),
    ]:
        active = []
        for i in range(low, high):
            x, y = (fixed, i) if dx else (i, fixed)
            fx, fy = x + 4 * dx, y + 4 * dy
            if (0 <= x < width and 0 <= y < height and 0 <= fx < width and 0 <= fy < height
                    and all(pixels[x + t * dx, y + t * dy] for t in (1, 2, 3, 4))):
                active.append(i)
        for _, group in groupby(enumerate(active), lambda item: item[1] - item[0]):
            run = [value for _, value in group]
            middle = run[len(run) // 2]
            ports.append(((fixed, middle) if dx else (middle, fixed), (dx, dy)))
    return ports


def _outward(a, b, direction):
    return (b[0] - a[0]) * direction[0] + (b[1] - a[1]) * direction[1] > 1


def _continues(point, direction, ink):
    pixels = ink.load()
    x, y = point
    dx, dy = direction
    return all(0 <= x + d * dx < ink.width and 0 <= y + d * dy < ink.height
               and pixels[x + d * dx, y + d * dy] for d in (3, 4, 5))


def _route(left, right, ink):
    a, ad = left
    b, bd = right
    if ad[0] == -bd[0] and ad[1] == -bd[1]:
        cross_axis = 1 if ad[0] else 0
        if abs(a[cross_axis] - b[cross_axis]) <= 2 and _outward(a, b, ad):
            return [a, b]
    elif bool(ad[0]) != bool(bd[0]):
        bend = (b[0], a[1]) if ad[0] else (a[0], b[1])
        if (_outward(a, bend, ad) and _outward(b, bend, bd)
                and not _continues(bend, ad, ink) and not _continues(bend, bd, ink)):
            return [a, bend, b]
    return None


def _stroke_fit(path, ink):
    """Check each leg in sections so a long intact run cannot hide a broken elbow."""
    pixels = ink.load()
    total_distance, total_coverage, count = 0.0, 0.0, 0
    for a, b in zip(path, path[1:]):
        samples = _samples(a, b, pixels, *ink.size)
        windows = max(1, ceil(len(samples) / 96))
        for index in range(windows):
            part = samples[index * len(samples) // windows:(index + 1) * len(samples) // windows]
            label, distance, coverage = classify_stroke(part)
            if label not in CONTINUOUS:
                return None
            total_distance += distance
            total_coverage += coverage * len(part)
            count += len(part)
    return total_distance, total_coverage / count


def extract_local_connections(image_path, classical_components=None, classical_arrows=None):
    """Run Windows OCR/geometry followed by a separate, learned route classifier.

    For paired experiments, callers may pass the image-only baseline's component
    observations and raw arrow landmarks to reuse its raster/OCR pass. No baseline edge, reference file, tag
    dictionary, image-specific model, or source geometry is used to trace lines.
    """
    source = Path(image_path)
    with Image.open(source) as image:
        ink = image.convert("L").point(lambda value: 255 if value < 160 else 0)
    if classical_components is None:
        with TemporaryDirectory(prefix="aip-v3-ocr-") as scratch:
            observed = extract_image(source, Path(scratch))
            classical_components = observed["components"]
            classical_arrows = observed.get("arrows", [])
    components = []
    for item in classical_components:
        component = {key: item[key] for key in ("id", "bbox", "type", "tag_bbox", "evidence", "uncertainty")
                     if key in item}
        component.update(tag=item.get("tag", item.get("observed_tag")) or "", review_status="unreviewed")
        components.append(component)
    ports = {item["id"]: _ports(item["bbox"], ink) for item in components}
    connections = []
    for left, right in combinations(components, 2):
        source_id, target_id = left["id"], right["id"]
        best = None
        for a, b in product(ports[source_id], ports[target_id]):
            path = _route(a, b, ink)
            if path is None or _blocked(path, components, source_id, target_id):
                continue
            fit = _stroke_fit(path, ink)
            if fit is not None:
                distance, coverage = fit
                length = sum(hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(path, path[1:]))
                candidate = (distance, length, path, coverage)
                if best is None or candidate < best:
                    best = candidate
        if best is not None:
            _, _, path, coverage = best
            connections.append({"id": f"ml-line-{len(connections) + 1}", "source": source_id,
                "target": target_id, "kind": "direct_undirected", "direction": "unknown",
                "path": [list(point) for point in path], "review_status": "unreviewed",
                "evidence": f"Stroke model accepted each leg between visible symbol contacts ({coverage:.0%} sampled ink).",
                "uncertainty": "Process-line type and any observed flow direction require drawing review."})
    arrows = raster_arrows(ink, components, connections, ports, classical_arrows or [])
    slack = max(2, min(8, round(min(ink.size) * 0.002)))
    assign_directions(ink, components, connections, arrows, ports, slack)
    return {"components": components, "connections": connections, "arrows": arrows, "connectors": [],
            "excluded_connections": [], "review_required": True, "provider": "local-ml", "model": MODEL_ID,
            "image": {"width": ink.width, "height": ink.height, "sha256": sha256(source.read_bytes()).hexdigest()},
            "training": {"generator_seed": TRAINING_SEED, "examples": EXAMPLES_PER_CLASS * len(CLASSES),
                         "classes": list(CLASSES), "source": "procedural stroke profiles"},
            "uncertainties": ["Traces straight runs and single elbows; turns at ambiguous crossings are withheld. "
                              "Missed symbols, more complex routes and unread equipment names can affect connectivity."]}


def _near_box(point, box, slack):
    x, y = point
    return box[0] - slack <= x <= box[2] + slack and box[1] - slack <= y <= box[3] + slack


def _dilate(ink, radius):
    """Local maximum in a square neighborhood, using bounded whole-image shifts."""
    value = ink
    for axis in ((1, 0), (0, 1)):
        done = 0
        while done < radius:
            step = min(done + 1, radius - done)
            wider = value
            for sign in (-1, 1):
                shifted = Image.new("L", ink.size)
                shifted.paste(value, (axis[0] * sign * step, axis[1] * sign * step))
                wider = ImageChops.lighter(wider, shifted)
            value = wider
            done += step
    return value


def _has_arrow_wings(arrow, ink):
    """Require ink at the tip and two flanks that widen toward the back of the head."""
    tx, ty = arrow["tip"]
    ex, ey = arrow["tail"]
    length = hypot(tx - ex, ty - ey)
    if length < 2:
        return False
    dx, dy = (tx - ex) / length, (ty - ey) / length
    near = [0.0, 0.0]
    far = [0.0, 0.0]
    tip_ink = False
    pixels = ink.load()
    l, t, r, b = arrow["bbox"]
    # Keep the probe on the reported head, bounded even for a bad model box.
    l, r = max(0, int(max(l, tx - 1.5 * length))), min(ink.width, ceil(min(r, tx + 1.5 * length)))
    t, b = max(0, int(max(t, ty - 1.5 * length))), min(ink.height, ceil(min(b, ty + 1.5 * length)))
    step = max(1, max(r - l, b - t) // 100)
    for y in range(t, b, step):
        for x in range(l, r, step):
            if not pixels[x, y]:
                continue
            if hypot(tx - x, ty - y) <= max(2, step):
                tip_ink = True
            behind = (tx - x) * dx + (ty - y) * dy
            side = (x - tx) * (-dy) + (y - ty) * dx
            if 0.15 * length <= behind <= 1.2 * length:
                band = near if behind < 0.55 * length else far
                sign = int(side > 0)
                band[sign] = max(band[sign], abs(side))
    return tip_ink and all(n >= 1 and f >= max(2, n + 1, 0.12 * length) for n, f in zip(near, far))


def _check_vision_routes(result, ink):
    """Keep failed provider traces as review evidence outside the direct graph."""
    by_id = {node["id"]: node for node in result["components"]}
    slack = max(2, min(8, round(min(ink.size) * 0.002)))
    support = _dilate(ink, slack)
    accepted, excluded = [], []
    for edge in result["connections"]:
        source, target, path = edge["source"], edge["target"], edge["path"]
        if (not _near_box(path[0], by_id[source]["bbox"], slack)
                and _near_box(path[-1], by_id[source]["bbox"], slack)
                and _near_box(path[0], by_id[target]["bbox"], slack)):
            path = edge["path"] = list(reversed(path))
        reason = None
        if not (_near_box(path[0], by_id[source]["bbox"], slack)
                and _near_box(path[-1], by_id[target]["bbox"], slack)):
            reason = "The traced endpoints do not reach both indicated symbol boxes."
        elif all(point == path[0] for point in path):
            reason = "The proposed path has zero length."
        elif _blocked(path, result["components"], source, target):
            reason = "The proposed direct path crosses an intervening marked symbol."
        elif not edge.get("evidence"):
            reason = "The model supplied no visible evidence for this path."
        elif edge["kind"] == "pipe" and _stroke_fit(path, support) is None:
            reason = "The proposed process line lacks continuous raster support."
        if reason:
            excluded.append({**edge, "exclusion_reason": reason})
            result["uncertainties"].append(f"{edge['id']}: {reason}")
        else:
            accepted.append(edge)
    by_edge = {edge["id"]: edge for edge in accepted}
    for arrow in result["arrows"]:
        edge_id = arrow.get("connection_id")
        arrow["verified"] = (all(_near_box(p, arrow["bbox"], slack) for p in (arrow["tail"], arrow["tip"]))
                             and _has_arrow_wings(arrow, ink))
        if edge_id is not None:
            if (edge_id not in by_edge or not arrow["verified"] or
                    not arrow_direction(arrow, by_edge[edge_id]["path"], slack)):
                arrow["proposed_connection_id"] = arrow.pop("connection_id")
                arrow["verified"] = False
                result["uncertainties"].append(f"{arrow['id']}: Arrow could not be aligned with its proposed line.")
    ports = {c["id"]: _ports(c["bbox"], ink) for c in result["components"]} if any(
        a["verified"] for a in result["arrows"]) else {}
    claimed = {edge["id"]: edge["direction"] for edge in accepted}
    assign_directions(ink, result["components"], accepted, result["arrows"], ports, slack)
    for edge in accepted:
        if claimed[edge["id"]] != "unknown" and claimed[edge["id"]] != edge["direction"]:
            result["uncertainties"].append(f"{edge['id']}: The proposed direction lacked matching arrow evidence.")
    result.update(connections=accepted, excluded_connections=excluded)
    return result


def extract_connections(settings, image_bytes, mime, width, height):
    """Return validated drawing-space predictions; never accepts reference annotations."""
    if not isinstance(image_bytes, bytes) or not 0 < len(image_bytes) <= MAX_IMAGE_BYTES:
        raise ProviderError("Choose a drawing image of up to 20 MiB.")
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise ProviderError("Render the drawing as an image before tracing connections.")
    with Image.open(BytesIO(image_bytes)) as image:
        if image.size != (width, height) or image.width * image.height > 30_000_000:
            raise ProviderError("The drawing dimensions disagree with the image or exceed 30 million pixels.")
        if Image.MIME.get(image.format) != mime:
            raise ProviderError("The drawing image format disagrees with its reported media type.")
        if settings.get("provider") == "local-ml":
            with TemporaryDirectory(prefix="aip-v3-image-") as scratch:
                path = Path(scratch) / "drawing.png"
                if mime == "image/png":
                    path.write_bytes(image_bytes)
                else:
                    image.convert("RGB").save(path)
                result = extract_local_connections(path)
                result["image"]["sha256"] = sha256(image_bytes).hexdigest()
                return result
        ink = image.convert("L").point(lambda value: 255 if value < 160 else 0)
    candidate = extract_provider(settings, image_bytes, mime, width, height)
    return _check_vision_routes(candidate, ink)
