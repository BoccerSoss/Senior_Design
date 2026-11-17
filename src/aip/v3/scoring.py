"""Whole-sheet scoring of immediate undirected equipment pairs in the displayed matrix."""
from __future__ import annotations

from math import isfinite

from ..core import normalize_tag
from ..v2.matrix import build_matrix


TARGET = 0.90
IOU_THRESHOLD = 0.50


def metrics(tp, fp, fn):
    denom = 2 * tp + fp + fn
    f1 = 2 * tp / denom if denom else None
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": f1, "gap_to_target": TARGET - f1 if f1 is not None else None,
    }


def _equipment(graph):
    # Apply the same exclusions and deduplication as the user-visible matrix.
    sheet = {**graph, "id": "page", "facility_id": "benchmark"}
    matrix = build_matrix({"facilities": [{"id": "benchmark"}], "sheets": [sheet]}, "benchmark", "page")
    if any(issue["severity"] == "error" for issue in matrix["diagnostics"]):
        raise ValueError("Malformed component or edge identity in benchmark graph.")
    by_id = {item["id"]: item for item in graph["components"]}
    nodes = {node["component_id"]: by_id[node["component_id"]] for node in matrix["nodes"]}
    pairs = {tuple(sorted((e["source"]["component_id"], e["target"]["component_id"]))) for e in matrix["direct_edges"]}
    return nodes, pairs


def _valid_box(box):
    return (isinstance(box, (tuple, list)) and len(box) == 4
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and isfinite(x) for x in box)
            and box[0] < box[2] and box[1] < box[3])


def _iou(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union


def _tag(item):
    return normalize_tag(item.get("tag", item.get("observed_tag")))


def match_equipment(predicted, truth):
    """Greedy descending IoU then IDs, independent of tags, types, and edges."""
    if any(not _valid_box(node.get("bbox")) for node in truth.values()):
        raise ValueError("Every reference equipment item must have a valid symbol bounding box.")
    candidates = []
    for prediction_id, node in predicted.items():
        if not _valid_box(node.get("bbox")):
            continue
        for reference_id, target in truth.items():
            iou = _iou(node["bbox"], target["bbox"])
            if iou >= IOU_THRESHOLD:
                candidates.append((-iou, prediction_id, reference_id))
    matched, used = {}, set()
    for _, prediction_id, reference_id in sorted(candidates):
        if prediction_id not in matched and reference_id not in used:
            matched[prediction_id] = reference_id
            used.add(reference_id)
    return matched


def _edge_result(observed, expected, mapping):
    correct = {}
    extra = []
    for pair in sorted(observed):
        a, b = pair
        translated = tuple(sorted((mapping[a], mapping[b]))) if a in mapping and b in mapping else None
        if translated in expected:
            correct[translated] = pair
        else:
            # Unidentified endpoints still cost a false positive.
            extra.append(pair)
    missing = sorted(expected - correct.keys())
    counts = metrics(len(correct), len(extra), len(missing))
    return counts, {"matched_reference_edges": sorted(correct), "extra_prediction_edges": extra,
                    "missing_reference_edges": missing}


def score_graph(prediction, reference):
    observed, edges = _equipment(prediction)
    expected, expected_edges = _equipment(reference)
    mapping = match_equipment(observed, expected)
    localized, local_details = _edge_result(edges, expected_edges, mapping)
    # An explicit null in a complete transcription means no equipment name is printed.
    labels_available = all("tag" in item or "observed_tag" in item for item in expected.values())
    named = named_details = None
    printed_names = None
    matched_ids = set(mapping.values())
    coverage = {"reference_pairs": len(expected_edges),
                "both_endpoints_located": sum(a in matched_ids and b in matched_ids for a, b in expected_edges),
                "both_endpoints_located_and_named": None}
    if labels_available:
        named_mapping = {a: b for a, b in mapping.items() if _tag(observed[a]) == _tag(expected[b])}
        named, named_details = _edge_result(edges, expected_edges, named_mapping)
        named_ids = set(named_mapping.values())
        printed = {ref_id for ref_id, item in expected.items() if _tag(item)}
        printed_names = {"expected": len(printed), "located": len(printed & matched_ids),
                         "correct": len(printed & named_ids)}
        coverage["both_endpoints_located_and_named"] = sum(a in named_ids and b in named_ids for a, b in expected_edges)
    return ({"named_edges": named, "localized_edges": localized,
             "endpoint_coverage": coverage, "printed_names": printed_names,
             "localized_nodes": metrics(len(mapping), len(observed) - len(mapping), len(expected) - len(mapping))},
            {"matched_nodes": mapping, "localized_edges": local_details, "named_edges": named_details})


def micro_average(scored):
    """Return null for metrics missing on any selected sheet, including failed runs."""
    result = {}
    for metric in ("named_edges", "localized_edges", "localized_nodes"):
        rows = [item.get(metric) if item else None for item in scored]
        result[metric] = (metrics(*(sum(row[name] for row in rows) for name in ("tp", "fp", "fn")))
                          if rows and all(row is not None for row in rows) else None)
    result["endpoint_coverage"] = None
    name_counts = [item.get("printed_names") if item else None for item in scored]
    result["printed_names"] = ({key: sum(row[key] for row in name_counts) for key in ("expected", "located", "correct")}
                               if name_counts and all(row is not None for row in name_counts) else None)
    if scored and all(item is not None for item in scored):
        coverage = {}
        for key in ("reference_pairs", "both_endpoints_located", "both_endpoints_located_and_named"):
            counts = [item["endpoint_coverage"][key] for item in scored]
            coverage[key] = sum(counts) if all(x is not None for x in counts) else None
        result["endpoint_coverage"] = coverage
    return result
