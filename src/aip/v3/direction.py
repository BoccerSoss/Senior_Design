"""Locate real arrowheads and relate them to individual equipment pairs on a straight run."""
from collections import defaultdict
from math import hypot

from PIL import Image

from ..connection_geometry import HorizontalStroke, arrowheads_on_stroke
from ..flow_routes import trace_arrow_routes


def _scan(mask, width, low, high, y):
    """Measure the narrow shaft away from a wing, a crossing or a small dropout."""
    if high - low < 20:
        return []
    height = len(mask) // width
    shafts = []
    for fraction in (0.25, 0.5, 0.75):
        x = round(low + (high - low) * fraction)
        if not mask[y * width + x]:
            continue
        top = bottom = y
        while top and mask[(top - 1) * width + x]:
            top -= 1
        while bottom + 1 < height and mask[(bottom + 1) * width + x]:
            bottom += 1
        shafts.append((bottom - top, top, bottom))
    if not shafts:
        return []
    _, top, bottom = min(shafts)
    if bottom - top > 12:
        return []
    return arrowheads_on_stroke(mask, width, HorizontalStroke(low, high, top, bottom))


def raster_arrows(ink, components, connections, ports, classical_arrows):
    """Reuse the image detector's landmarks, then check pipe legs and their exposed ends."""
    arrows = []

    def add(tip, wings):
        tail = [sum(p[i] for p in wings) / 2 for i in (0, 1)]
        vx, vy = tip[0] - tail[0], tip[1] - tail[1]
        for arrow in arrows:
            ax, ay = arrow["tip"]
            dx, dy = ax - arrow["tail"][0], ay - arrow["tail"][1]
            if hypot(ax - tip[0], ay - tip[1]) <= 6 and dx * vx + dy * vy > 0:
                return
        points = [tip, *wings]
        bbox = [min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points) + 1, max(p[1] for p in points) + 1]
        arrows.append({"id": f"ml-arrow-{len(arrows) + 1}", "bbox": bbox, "tip": list(tip),
                       "tail": tail, "wings": wings, "review_status": "unreviewed", "verified": True})

    for arrow in classical_arrows:
        add(arrow["arrow_tip"], arrow["arrow_wings"])
    masks = {}

    def mask_for(vertical):
        if vertical not in masks:
            raster = ink.transpose(Image.Transpose.TRANSPOSE) if vertical else ink
            masks[vertical] = raster.tobytes(), raster.width
        return masks[vertical]

    def scan(vertical, low, high, y):
        mask, width = mask_for(vertical)
        for head in _scan(mask, width, low, high, y):
            tip, wings = head["arrow_tip"], head["arrow_wings"]
            if vertical:
                tip, wings = tip[::-1], [w[::-1] for w in wings]
            add(tip, wings)

    used_ports = set()
    for edge in connections:
        path = edge["path"]
        used_ports.update(((edge["source"], tuple(path[0])), (edge["target"], tuple(path[-1]))))
        for a, b in zip(path, path[1:]):
            vertical = abs(a[0] - b[0]) < abs(a[1] - b[1])
            first, last = (a[::-1], b[::-1]) if vertical else (a, b)
            if abs(first[1] - last[1]) <= 2:
                scan(vertical, min(first[0], last[0]), max(first[0], last[0]), (first[1] + last[1]) // 2)

    # A terminal arrow need not fall between two pieces of equipment. Search a
    # connected outgoing stem up to the next symbol or first break in the ink.
    for item in components:
        for point, direction in ports[item["id"]]:
            if (item["id"], point) in used_ports:
                continue
            vertical = direction[1] != 0
            x, y = point[::-1] if vertical else point
            step = direction[1] if vertical else direction[0]
            mask, width = mask_for(vertical)
            limit = width if step > 0 else -1
            for other in components:
                if other["id"] == item["id"]:
                    continue
                l, t, r, b = map(round, other["bbox"])
                if vertical:
                    l, t, r, b = t, l, b, r
                if t <= y < b:
                    if step > 0 and x < l < limit:
                        limit = l
                    if step < 0 and limit < r - 1 < x:
                        limit = r - 1
            end = x
            while end + step != limit and mask[y * width + end + step]:
                end += step
            if abs(end - x) >= 20:
                # Search past the physical tip so the open wings are not clipped at the crop boundary.
                padded = min(width - 1, end + 6) if step > 0 else max(0, end - 6)
                scan(vertical, min(x, padded), max(x, padded), y)
    return arrows


def _axial_routes(ink, arrows, components):
    """Trace upstream on actual ink. Transpose vertical runs into the shared shaft walker."""
    for vertical in (False, True):
        marks = []
        for arrow in arrows:
            tip, tail = arrow["tip"], arrow["tail"]
            dx, dy = tip[0] - tail[0], tip[1] - tail[1]
            along, across = (dy, dx) if vertical else (dx, dy)
            if not along or abs(across) > 0.2 * abs(along):
                continue
            tip = [round(value) for value in tip]
            wings = arrow.get("wings", [tail, tail])
            marks.append({"id": arrow["id"], "direction": "right" if along > 0 else "left",
                          "arrow_tip": tip[::-1] if vertical else tip,
                          "arrow_wings": [p[::-1] for p in wings] if vertical else wings})
        if not marks:
            continue
        raster = ink.transpose(Image.Transpose.TRANSPOSE) if vertical else ink
        if vertical:
            boxes = [{"id": node["id"], "bbox": [node["bbox"][i] for i in (1, 0, 3, 2)]}
                     for node in components]
        else:
            boxes = components
        for route in trace_arrow_routes(raster.tobytes(), raster.width, marks, boxes):
            yield route, vertical


def arrow_direction(arrow, path, slack):
    tip, tail = arrow["tip"], arrow["tail"]
    vx, vy = tip[0] - tail[0], tip[1] - tail[1]
    if not (vx or vy):
        return None
    nearest = None
    for a, b in zip(path, path[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = hypot(dx, dy)
        if not length:
            continue
        t = max(0, min(1, ((tip[0] - a[0]) * dx + (tip[1] - a[1]) * dy) / length**2))
        distance = hypot(tip[0] - (a[0] + t * dx), tip[1] - (a[1] + t * dy))
        cosine = (vx * dx + vy * dy) / (length * hypot(vx, vy))
        if distance <= slack + 1 and abs(cosine) >= 0.7 and (nearest is None or distance < nearest[0]):
            nearest = (distance, "source_to_target" if cosine > 0 else "target_to_source")
    return nearest[1] if nearest else None


def _has_branch(path, arrow, ink, slack):
    """Check an axial edge for a perpendicular arm before assigning flow to the whole pair."""
    pixels = ink.load()
    head = arrow["bbox"]
    for a, b in zip(path, path[1:]):
        axis = int(abs(b[1] - a[1]) > abs(b[0] - a[0]))
        cross = 1 - axis
        if abs(b[cross] - a[cross]) > slack:
            continue
        low, high = sorted((round(a[axis]), round(b[axis])))
        for along in range(low + slack + 2, high - slack - 1):
            fraction = (along - a[axis]) / (b[axis] - a[axis])
            middle = round(a[cross] + fraction * (b[cross] - a[cross]))
            x, y = (middle, along) if axis else (along, middle)
            for sign in (-1, 1):
                reach = 10
                if head[axis] - slack <= along <= head[axis + 2] + slack:
                    wing = head[cross + (2 if sign > 0 else 0)]
                    reach = max(reach, round((wing - middle) * sign) + slack + 2)
                end = middle + sign * reach
                if not 0 <= end < ink.size[cross]:
                    continue
                if all(pixels[x + (sign * n if axis else 0), y + (0 if axis else sign * n)]
                       for n in range(1, reach + 1)):
                    return True
    return False


def assign_directions(ink, components, connections, arrows, ports, slack):
    """Attach a head once, and carry its direction only along evidenced neighboring pairs."""
    incident = defaultdict(list)
    pairs = defaultdict(list)
    direct = defaultdict(dict)
    inferred = defaultdict(dict)
    for edge in connections:
        incident[edge["source"]].append(edge)
        incident[edge["target"]].append(edge)
        pairs[frozenset((edge["source"], edge["target"]))].append(edge)
    verified = []
    for arrow in arrows:
        if not arrow.get("verified"):
            continue
        matches = []
        for edge in connections:
            direction = arrow_direction(arrow, edge["path"], slack)
            if direction:
                matches.append((edge, direction))
        if len(matches) > 1:
            if "connection_id" in arrow:
                arrow["proposed_connection_id"] = arrow.pop("connection_id")
            continue
        if matches:
            edge, direction = matches[0]
            arrow["connection_id"] = edge["id"]
            if not _has_branch(edge["path"], arrow, ink, slack):
                direct[edge["id"]][arrow["id"]] = direction
        verified.append(arrow)
    stops = {node["id"] for node in components
             if node.get("type") == "junction" or len(incident[node["id"]]) > 2 or len(ports.get(node["id"], [])) > 2}
    for route, vertical in _axial_routes(ink, verified, components):
        downstream = list(reversed(route["component_ids"]))
        axis = 0 if vertical else 1
        shaft = route["path"][-1][1]  # The walker reports paths in transposed space for vertical runs.
        for target, source in zip(downstream, downstream[1:]):
            edges = pairs[frozenset((source, target))]
            if len(edges) != 1 or target in stops:
                break
            edge = edges[0]
            if (edge["kind"] not in {"pipe", "direct_undirected"}
                    or any(abs(p[axis] - shaft) > slack + 2 for p in edge["path"])):
                break
            direction = "source_to_target" if edge["source"] == source else "target_to_source"
            inferred[edge["id"]][route["arrow_id"]] = direction
            if source in stops:
                break
    for edge in connections:
        observed = direct[edge["id"]]
        votes = {**inferred[edge["id"]], **observed}
        edge["direction"] = "unknown"
        if not votes:
            continue
        if len(set(votes.values())) > 1:
            basis = "conflict"
        else:
            basis = "on_edge" if observed else "run"
            edge["direction"] = next(iter(votes.values()))
        edge["direction_evidence"] = {"basis": basis, "arrow_ids": list(votes)}
