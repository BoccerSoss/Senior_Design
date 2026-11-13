"""Provisional reference evaluation, isolated from inference/normalization."""
from __future__ import annotations

import re

from .core import ValidationError, normalize_tag, validate_document


def _metric(predicted, expected):
    correct, extra, missing = predicted & expected, predicted - expected, expected - predicted
    tp, fp, fn = len(correct), len(extra), len(missing)
    def serial(values):
        return [list(x) if isinstance(x, tuple) else x for x in sorted(values)]
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "correct": serial(correct), "missing": serial(missing), "extra": serial(extra)}


def _reference(reference):
    if not isinstance(reference, dict):
        raise ValidationError("reference must be a JSON object")
    errors, ids, tags, types = [], {}, set(), set()
    if "drawing_id" in reference and (not isinstance(reference["drawing_id"], str) or not reference["drawing_id"].strip()):
        errors.append("reference.drawing_id must be nonempty text")
    if "image" in reference:
        image = reference["image"]
        if not isinstance(image, dict):
            errors.append("reference.image must be a metadata object")
        else:
            if "sha256" in image and (not isinstance(image["sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", image["sha256"])):
                errors.append("reference.image.sha256 must contain 64 hexadecimal characters")
            for key in ("width", "height"):
                if key in image and (isinstance(image[key], bool) or not isinstance(image[key], int) or image[key] <= 0):
                    errors.append(f"reference.image.{key} must be a positive integer")
    components = reference.get("components")
    edges = reference.get("edges")
    if not isinstance(components, list) or not isinstance(edges, list):
        raise ValidationError("reference.components and reference.edges must be arrays")
    for i, item in enumerate(components):
        if not isinstance(item, dict):
            errors.append(f"reference.components[{i}] must be an object")
            continue
        ident = item.get("id")
        try:
            tag = normalize_tag(item.get("tag", item.get("normalized_tag")))
        except ValidationError:
            tag = None
        typ = item.get("type")
        if not isinstance(ident, str) or not ident or ident in ids:
            errors.append(f"reference.components[{i}] has missing/duplicate ID")
            continue
        if tag is None or tag in tags:
            errors.append(f"reference.components[{i}] has missing/duplicate normalized tag")
            continue
        if not isinstance(typ, str) or not typ.strip():
            errors.append(f"reference.components[{i}].type is required")
            continue
        ids[ident] = tag
        tags.add(tag)
        types.add((tag, typ.strip().lower()))
    pairs = set()
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(f"reference.edges[{i}] must be an object")
            continue
        a, b = edge.get("source"), edge.get("target")
        if not isinstance(a, str) or not isinstance(b, str) or a not in ids or b not in ids or a == b:
            errors.append(f"reference.edges[{i}] has dangling endpoints or self-loop")
            continue
        pair = tuple(sorted((ids[a], ids[b])))
        if pair in pairs:
            errors.append(f"reference.edges[{i}] duplicates an undirected edge")
        pairs.add(pair)
    if reference.get("directed") not in (False, None):
        errors.append("reference must use undirected immediate adjacency")
    if "adjacency_matrix" in reference or "matrix_order" in reference:
        order, matrix = reference.get("matrix_order"), reference.get("adjacency_matrix")
        if (not isinstance(order, list) or any(not isinstance(x, str) for x in order)
                or len(order) != len(ids) or len(set(order)) != len(order) or set(order) != set(ids)):
            errors.append("reference.matrix_order must contain each reference ID once")
        elif not isinstance(matrix, list) or len(matrix) != len(order) or any(not isinstance(row, list) or len(row) != len(order) for row in matrix):
            errors.append("reference.adjacency_matrix dimensions disagree with matrix_order")
        else:
            for i, a in enumerate(order):
                for j, b in enumerate(order):
                    expected = int(i != j and tuple(sorted((ids[a], ids[b]))) in pairs)
                    if isinstance(matrix[i][j], bool) or matrix[i][j] not in (0, 1) or matrix[i][j] != expected:
                        errors.append(f"reference.adjacency_matrix[{i}][{j}] disagrees with direct undirected edges")
    if errors:
        raise ValidationError(errors)
    return tags, types, pairs


def evaluate(prediction: dict, reference: dict) -> dict:
    """Match tags, (tag,type), and unordered immediate-edge tag pairs separately."""
    validate_document(prediction)
    expected_tags, expected_types, expected_pairs = _reference(reference)
    drawing_id = reference.get("drawing_id")
    evaluation_warnings = list(prediction["warnings"])
    ref_image = reference.get("image", {})
    if isinstance(ref_image, dict):
        digest = ref_image.get("sha256")
        accepted_digests = {prediction["drawing"]["sha256"].lower()}
        if isinstance(prediction["drawing"].get("source_sha256"), str):
            accepted_digests.add(prediction["drawing"]["source_sha256"].lower())
        if digest and digest.lower() not in accepted_digests:
            raise ValidationError("prediction input SHA-256 differs from reference image; do not score a changed control against the clean image")
        for key in ("width", "height"):
            if ref_image.get(key) is not None and ref_image[key] != prediction["drawing"][key]:
                raise ValidationError(f"prediction {key} differs from reference image")
    if drawing_id is not None and drawing_id != prediction["drawing"]["drawing_id"]:
        if not isinstance(ref_image, dict) or not ref_image.get("sha256"):
            raise ValidationError("prediction and reference drawing_id differ and reference has no image SHA-256 to bind their identities")
        evaluation_warnings.append("Prediction and reference use different drawing IDs; mapped by matching original/prepared image SHA-256, not reference topology.")
    by_id = {c["id"]: c for c in prediction["components"]}
    tags = {c["normalized_tag"] for c in by_id.values() if c["normalized_tag"] is not None}
    types = {(c["normalized_tag"], c["type"]) for c in by_id.values() if c["normalized_tag"] is not None}
    pairs, unscored_connections = set(), []
    for edge in prediction["connections"]:
        a, b = by_id[edge["source"]]["normalized_tag"], by_id[edge["target"]]["normalized_tag"]
        if a is None or b is None:
            unscored_connections.append(edge)
        else:
            pairs.add(tuple(sorted((a, b))))
    unresolved = [c for c in prediction["components"] if c["normalized_tag"] is None]
    status = reference.get("review_status", "review status unknown")
    # Any unspecified, pending, or machine-only status remains provisional.
    accepted = status in {"independently human verified", "independent human review complete", "human verified"}
    return {
        "schema_version": "1.0.0", "drawing_id": prediction["drawing"]["drawing_id"],
        "reference_drawing_id": drawing_id,
        "run_id": prediction["run"]["run_id"], "validation_status": "valid",
        "scoring_status": "provisional" if not accepted else "reference human verified",
        "reference_review_status": status,
        "matching_rules": "Exact deterministic normalized tag; component types scored separately as (tag,type); unordered direct-edge tag pairs. No truth-based OCR correction.",
        "components": _metric(tags, expected_tags), "component_types": _metric(types, expected_types),
        "connections": _metric(pairs, expected_pairs),
        "unresolved": {"component_count": len(unresolved), "components": unresolved,
                       "connection_count": len(unscored_connections), "connections": unscored_connections},
        "uncertainties": prediction["uncertainties"], "warnings": evaluation_warnings,
        "limits": "Unresolved identities are retained outside tag-based metrics and reported above. Missing reference tags still count as FN. Provisional engineering evaluation is not official scoring or evidence of general P&ID performance.",
    }
