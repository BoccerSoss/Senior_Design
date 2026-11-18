"""Trace direct equipment pairs along the connected ink centerline."""
from collections import defaultdict, deque
from functools import cache
from math import hypot

try:
    import numpy as np
except ModuleNotFoundError as error:
    if error.name != "numpy":
        raise
    np = None


OFFSETS = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))
MAX_TEES = 2
SPUR_LENGTH = 18


def _thinning_tables():
    """Encode the two Zhang–Suen removal tests for all eight-neighbor patterns."""
    tables = np.zeros((2, 256), dtype=bool)
    for code in range(256):
        around = [(code >> i) & 1 for i in range(8)]
        if not 2 <= sum(around) <= 6:
            continue
        if sum(not first and second for first, second in zip(around, around[1:] + around[:1])) != 1:
            continue
        north, east, south, west = around[::2]
        tables[0, code] = not (north and east and south or east and south and west)
        tables[1, code] = not (north and east and west or north and south and west)
    return tables


_THINNING = _thinning_tables() if np is not None else ()
_DOT_PROBES = sorted((dx * dx + dy * dy, dx, dy)
                     for dx in range(-16, 17) for dy in range(-16, 17) if dx * dx + dy * dy <= 256)
_ARROW_PROBES = [(dx, dy) for d, dx, dy in _DOT_PROBES if d <= 64]


def _centerlines(ink):
    """Thin connected raster strokes while preserving blank gaps."""
    bounds = ink.getbbox()
    if bounds is None:
        return np.zeros((ink.height, ink.width), dtype=bool)
    left, top, right, bottom = bounds
    layer = np.pad(np.asarray(ink.crop(bounds)) != 0, 1)
    center = layer[1:-1, 1:-1]
    codes = np.empty(center.shape, dtype=np.uint8)
    neighbors = (layer[:-2, 1:-1], layer[:-2, 2:], layer[1:-1, 2:], layer[2:, 2:],
                 layer[2:, 1:-1], layer[2:, :-2], layer[1:-1, :-2], layer[:-2, :-2])
    while True:
        changed = False
        for table in _THINNING:
            codes.fill(0)
            for side in reversed(neighbors):
                codes <<= 1
                codes |= side
            removed = center & table[codes]
            if removed.any():
                center[removed] = False
                changed = True
        if not changed:
            skeleton = np.zeros((ink.height, ink.width), dtype=bool)
            skeleton[top:bottom, left:right] = center
            return skeleton


def _network(skeleton, components):
    """Stop raster routes at equipment boxes and condense intermediate pixels into arcs."""
    height, width = skeleton.shape
    owner = np.full((height, width), -1, dtype=np.int32)
    boxes = [tuple(map(round, item["bbox"])) for item in components]
    for ident, (left, top, right, bottom) in sorted(
            enumerate(boxes), key=lambda pair: -(pair[1][2] - pair[1][0]) * (pair[1][3] - pair[1][1])):
        owner[top:bottom, left:right] = ident
    ys, xs = np.nonzero(skeleton & (owner < 0))
    pixels = set(zip(xs.tolist(), ys.tolist()))
    adjacency = {}
    terminals = {}
    for x, y in pixels:
        adjacent = []
        equipment = set()
        for dx, dy in OFFSETS:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if not skeleton[ny, nx]:
                continue
            if dx and dy and (skeleton[y, nx] or skeleton[ny, x]):
                continue
            ident = owner[ny, nx]
            if ident < 0:
                adjacent.append((nx, ny))
            else:
                equipment.add(int(ident))
        point = (x, y)
        adjacency[point] = adjacent
        if equipment:
            terminals[point] = equipment

    decisions = {point for point, adjacent in adjacency.items() if len(adjacent) != 2 or point in terminals}
    arcs = []
    neighbors = defaultdict(list)
    traversed = set()
    for start in sorted(decisions):
        for adjacent in adjacency[start]:
            if (start, adjacent) in traversed:
                continue
            points = [start, adjacent]
            while points[-1] not in decisions:
                first, second = adjacency[points[-1]]
                points.append(second if first == points[-2] else first)
            end = points[-1]
            traversed.update(((start, adjacent), (end, points[-2])))
            ident = len(arcs)
            arcs.append(points)
            neighbors[start].append((ident, False))
            neighbors[end].append((ident, True))
    return arcs, neighbors, terminals


def _pruned_arcs(arcs, neighbors, terminals):
    """Skip short attached spurs such as arrow wings, preserving equipment contacts."""
    removed = set()
    pending = [point for point, attached in neighbors.items() if len(attached) == 1 and point not in terminals]
    while pending:
        tip = pending.pop()
        left = [(arc, reverse) for arc, reverse in neighbors[tip] if arc not in removed]
        if len(left) != 1:
            continue
        ident, reverse = left[0]
        points = arcs[ident]
        other = points[0] if reverse else points[-1]
        # A short exposed arm can still define a true four-way crossing.
        # Removing it would turn the crossing into a false tee.
        if len(points) - 1 <= SPUR_LENGTH and len(neighbors[other]) < 4:
            removed.add(ident)
            if other not in terminals:
                pending.append(other)
    return removed


def _arrow_votes(arcs, arrows):
    if not arrows:
        return {}
    samples = defaultdict(list)
    for ident, points in enumerate(arcs):
        for step, point in enumerate(points):
            samples[point].append((ident, step))
    votes = defaultdict(set)
    for arrow in arrows:
        if not arrow.get("verified"):
            continue
        tx, ty = arrow["tip"]
        px, py = arrow["tail"]
        vx, vy = tx - px, ty - py
        length = hypot(vx, vy)
        if length < 2:
            continue
        candidates = {}
        for dx, dy in _ARROW_PROBES:
            x, y = round(tx) + dx, round(ty) + dy
            for ident, step in samples.get((x, y), ()):
                points = arcs[ident]
                a, b = points[max(0, step - 10)], points[min(len(points) - 1, step + 10)]
                ax, ay = b[0] - a[0], b[1] - a[1]
                cosine = (vx * ax + vy * ay) / max(1, length * hypot(ax, ay))
                if abs(cosine) >= 0.86:
                    key = ident, 1 if cosine > 0 else -1
                    candidates[key] = min(candidates.get(key, float("inf")), hypot(x - tx, y - ty))
        ranked = sorted((distance, ident, sign) for (ident, sign), distance in candidates.items())
        if ranked and (len(ranked) == 1 or ranked[1][0] > ranked[0][0] + 1):
            _, ident, sign = ranked[0]
            votes[ident].add(sign)
    return votes


def _stable_contacts(ink, terminals, components, ports):
    pixels = ink.load()
    supported = {}
    for point, candidates in terminals.items():
        x, y = point
        supported[point] = {}
        for index in candidates:
            nearby = [(px, py) for (px, py), (dx, dy) in ports[components[index]["id"]]
                      if hypot(x - px, y - py) <= 6 and (x - px) * dx + (y - py) * dy > 0]
            if nearby:
                px, py = min(nearby, key=lambda p: (hypot(x - p[0], y - p[1]), p))
                steps = max(1, abs(px - x), abs(py - y))
                # Extend a one-pixel boundary contact only through its actual ink.
                straight = all(pixels[round(x + n * (px - x) / steps), round(y + n * (py - y) / steps)]
                               for n in range(1, steps + 1))
                supported[point][index] = (px, py) if straight else point
    return supported


def _simplify(points):
    """Keep the observed centerline bends while dropping subpixel-scale stair steps."""
    included = {0, len(points) - 1}
    pending = [(0, len(points) - 1)]
    while pending:
        start, end = pending.pop()
        ax, ay = points[start]
        bx, by = points[end]
        dx, dy = bx - ax, by - ay
        square = dx * dx + dy * dy
        largest, peak = 0.81, None
        for index in range(start + 1, end):
            x, y = points[index]
            part = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / square)) if square else 0
            distance = (x - ax - part * dx) ** 2 + (y - ay - part * dy) ** 2
            if distance > largest:
                largest, peak = distance, index
        if peak is not None:
            included.add(peak)
            pending.extend(((start, peak), (peak, end)))
    return [list(points[index]) for index in sorted(included)]


def _equipment_routes(ink, arcs, neighbors, terminals, contacts, votes):
    removed = _pruned_arcs(arcs, neighbors, terminals)
    active = {point: [(ident, reverse) for ident, reverse in attached if ident not in removed]
              for point, attached in neighbors.items()}
    positions = defaultdict(list)
    for point, equipment in contacts.items():
        for index in equipment:
            positions[index].append(point)
    pixels = ink.load()

    def far_point(ident, reverse, length):
        points = arcs[ident]
        return points[max(-len(points), -1 - length)] if reverse else points[min(len(points) - 1, length)]

    @cache
    def radius_squared(point):
        x, y = point
        return next((r for r, dx, dy in _DOT_PROBES
                     if not (0 <= x + dx < ink.width and 0 <= y + dy < ink.height and pixels[x + dx, y + dy])), 256)

    @cache
    def has_dot(point):
        attached = active[point]
        if len(attached) != 4:
            return False
        arm = max(radius_squared(far_point(arc, reverse, 11)) for arc, reverse in attached)
        return arm > 0 and radius_squared(point) >= 1.7**2 * arm

    @cache
    def opposite_arm(point, incoming, back):
        ax, ay = far_point(incoming, back, 10)
        dx, dy = ax - point[0], ay - point[1]
        ranked = []
        for ident, reverse in active[point]:
            if (ident, reverse) == (incoming, back):
                continue
            bx, by = far_point(ident, reverse, 10)
            vx, vy = bx - point[0], by - point[1]
            cosine = -(vx * dx + vy * dy) / max(1, hypot(dx, dy) * hypot(vx, vy))
            ranked.append((cosine, ident, reverse))
        ranked.sort(reverse=True)
        if ranked and ranked[0][0] >= 0.8 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.15):
            return [ranked[0][1:]]
        return []

    @cache
    def arm_direction(first, reverse):
        followed = set()
        directions = set()
        while first not in followed:
            followed.add(first)
            directions.update(-sign if reverse else sign for sign in votes.get(first, ()))
            end = arcs[first][0] if reverse else arcs[first][-1]
            attached = active[end]
            if end in terminals or len(attached) != 2:
                break
            onward = [(ident, rev) for ident, rev in attached if ident != first]
            if len(onward) != 1:
                break
            first, reverse = onward[0]
        return next(iter(directions)) if len(directions) == 1 else 0

    found = {}
    for source, start_points in sorted(positions.items()):
        for start in sorted(start_points):
            if terminals[start] - {source}:
                continue
            pending = deque([(start, None, False, 0, 0, ())])
            visited = {}
            while pending:
                point, incoming, back, tees, length, path = pending.popleft()
                state = point, incoming, back
                if state in visited and visited[state] <= (tees, length):
                    continue
                visited[state] = tees, length
                if point != start and point in terminals:
                    for target in contacts[point]:
                        if target == source:
                            continue
                        key = tuple(sorted((source, target)))
                        candidate = (tees, length, path if source < target else tuple(
                            (ident, not reverse) for ident, reverse in reversed(path)))
                        if path and (key not in found or candidate < found[key]):
                            found[key] = candidate
                    continue
                attached = active.get(point, ())
                options = [(ident, reverse) for ident, reverse in attached if (ident, reverse) != (incoming, back)]
                if incoming is not None and len(attached) > 2:
                    if len(attached) >= 4 and not has_dot(point):
                        options = opposite_arm(point, incoming, back)
                    else:
                        if tees == MAX_TEES:
                            continue
                        tees += 1
                        arm = arm_direction(incoming, back)
                        if arm:
                            options = [(ident, reverse) for ident, reverse in options
                                       if arm_direction(ident, reverse) != arm]
                for ident, reverse in options:
                    points = arcs[ident]
                    end = points[0] if reverse else points[-1]
                    pending.append((end, ident, not reverse, tees, length + len(points) - 1, path + ((ident, reverse),)))
    return found


def trace_raster_connections(ink, components, ports, arrows):
    """Return continuous pixel routes between directly neighboring equipment boxes."""
    if np is None or len(components) < 2:
        return []
    arcs, neighbors, terminals = _network(_centerlines(ink), components)
    contacts = _stable_contacts(ink, terminals, components, ports)
    if not any(contacts.values()):
        return []
    votes = _arrow_votes(arcs, arrows)
    connections = []
    for (source, target), (tees, _, trail) in sorted(
            _equipment_routes(ink, arcs, neighbors, terminals, contacts, votes).items()):
        points = []
        for ident, reverse in trail:
            leg = arcs[ident]
            segment = leg[::-1] if reverse else leg
            points.extend(segment[1:] if points else segment)
        start, end = contacts[points[0]][source], contacts[points[-1]][target]
        if start != points[0]:
            points.insert(0, start)
        if end != points[-1]:
            points.append(end)
        connections.append({
            "id": f"raster-line-{len(connections) + 1}",
            "source": components[source]["id"], "target": components[target]["id"],
            "kind": "direct_undirected", "direction": "unknown", "path": _simplify(points),
            "review_status": "unreviewed",
            "evidence": (f"The printed line reaches both equipment contacts through {tees} "
                         f"pipe junction{'s' if tees > 1 else ''}."
                         if tees else "The printed line reaches both equipment contacts."),
            "uncertainty": "The connected strokes identify drawing adjacency. Line service is unspecified.",
        })
    return connections
