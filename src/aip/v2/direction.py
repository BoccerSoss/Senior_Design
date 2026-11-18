"""Check directions on recorded drawing routes against the original image."""
from copy import deepcopy
from pathlib import Path

from ..v3.vision import refresh_trace_directions


DIRECTION_REVISION = 1


def _checked_arrow(mark, arrow_id):
    """Convert a source-checked or raster-observed arrow to the shared geometry."""
    tip, wings = mark.get("arrow_tip"), mark.get("arrow_wings")
    if not tip or not wings or len(wings) != 2:
        return None
    xs, ys = zip(tip, *wings)
    return {
        **mark, "id": arrow_id, "tip": tip, "wings": wings, "verified": True,
        "tail": [sum(xs[1:]) / 2, sum(ys[1:]) / 2],
        "bbox": [min(xs), min(ys), max(xs) + 1, max(ys) + 1],
    }


def check_directions(image_path, components, connections, arrows=(), *, reference_routes=False, protected=()):
    """Return direction evidence and arrow marks without replacing any saved route."""
    retained = deepcopy(connections)
    visible = []
    by_tip = {}
    for index, mark in enumerate(deepcopy(arrows), 1):
        arrow = mark if "tip" in mark else _checked_arrow(mark, mark.get("id", f"arrow-{index}"))
        if arrow:
            visible.append(arrow)
            if arrow.get("verified"):
                by_tip[tuple(arrow["tip"])] = arrow["id"]

    source_arrows = {}
    for edge in retained:
        observed = edge.get("observed_arrow") or {}
        if observed.get("status") in (None, "unknown") or not observed.get("arrow_tip"):
            continue
        tip = tuple(observed["arrow_tip"])
        arrow_id = by_tip.get(tip)
        if arrow_id is None:
            arrow_id = f"{edge['id']}-source-arrow"
            arrow = _checked_arrow(observed, arrow_id)
            if not arrow:
                continue
            visible.append(arrow)
            by_tip[tip] = arrow_id
        source_arrows[edge["id"]] = arrow_id

    boxes = {component["id"] for component in components if component.get("bbox")}
    routes = [edge for edge in retained if edge.get("path") and edge["source"] in boxes and edge["target"] in boxes]
    if not routes:
        return retained, arrows
    trace = {
        "components": [component for component in components if component["id"] in boxes],
        "connections": routes, "arrows": visible,
    }
    checked = refresh_trace_directions(trace, Path(image_path).read_bytes(), reference_routes=reference_routes)
    updated = {edge["id"]: edge for edge in checked["connections"]}
    protected = set(protected)
    for edge in retained:
        edge_id = edge["id"]
        if edge_id in protected or edge.get("review_status") in {"confirmed", "rejected"}:
            continue
        direction = edge.get("direction", "unknown")
        candidate = updated.get(edge_id, {})
        inferred = candidate.get("direction", "unknown")
        evidence = candidate.get("direction_evidence")
        if edge_id in source_arrows and direction != "unknown":
            edge["direction_evidence"] = {"basis": "on_edge", "arrow_ids": [source_arrows[edge_id]]}
        elif evidence and (direction == "unknown" or direction == inferred):
            edge["direction"] = inferred
            edge["direction_evidence"] = evidence
    return retained, checked["arrows"]


def refresh_drawing_directions(sheet, image_path):
    """Prepare a direction update for an existing V2 sheet and keep its human edits."""
    metadata = sheet["source_metadata"]
    analysis = metadata.get("analysis")
    if not analysis:
        return sheet
    protected = {correction["id"] for correction in sheet.get("corrections", ())
                 if correction["collection"] == "connections"}
    connections, arrows = check_directions(
        image_path, sheet["components"], sheet["connections"], analysis.get("arrows", ()),
        reference_routes=analysis.get("method") == "drawing-reference", protected=protected,
    )
    return {
        **sheet, "connections": connections, "source_metadata": {
            **metadata, "analysis": {**analysis, "arrows": arrows, "direction_revision": DIRECTION_REVISION},
        },
    }
