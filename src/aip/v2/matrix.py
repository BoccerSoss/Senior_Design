"""Direct equipment adjacency from saved observations, without graph inference.

The initial project convention is undirected immediate equipment adjacency. A
matrix entry never comes from reachability, inferred line crossings, isolation
rules, or the reference answer. Junction/continuation contraction is deliberately
outside this convention and is reported rather than silently creating edges.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import csv
import io

from ..core import normalize_tag


TOPOLOGY_TYPES = {
    "junction", "pipe junction", "junction point", "tee", "connector",
    "off page connector", "line continuation", "open end", "source", "sink",
    "pipe", "process pipe", "signal", "signal line", "line", "arrow",
    "text", "label", "legend", "title",
}
DIRECT_KINDS = {"pipe", "direct_undirected"}


def _type(value):
    return " ".join(str(value or "").strip().lower().replace("-", " ").replace("_", " ").split())


def _identifier(value):
    return isinstance(value, str) and bool(value.strip())


def _ref(node):
    return {"sheet_id": node["sheet_id"], "component_id": node["component_id"]}


def build_matrix(state, facility_id, sheet_id=None, *, include_unreviewed_warnings=True):
    """Return labeled binary direct adjacency and explicit excluded observations.

    Pass the selected ``sheet_id`` for the normal drawing workflow. Explicitly
    passing ``None`` requests a block-diagonal matrix for active facility sheets;
    connector links are not direct equipment edges. Saved component order is the
    matrix order. Unread names are retained using unique source observation IDs.
    A zero means no included direct relation, not proof of physical disconnection.
    """
    if not _identifier(facility_id) or not any(
        facility.get("id") == facility_id for facility in state.get("facilities", ())
    ):
        raise ValueError("Choose an existing drawing collection.")
    facility_sheets = [sheet for sheet in state.get("sheets", ()) if sheet.get("facility_id") == facility_id]
    if sheet_id is not None:
        if not _identifier(sheet_id):
            raise ValueError("Choose a drawing for the matrix.")
        sheets = [sheet for sheet in facility_sheets if sheet.get("id") == sheet_id]
        if len(sheets) != 1:
            raise ValueError("The selected drawing was not found in this collection or has a duplicate ID.")
    else:
        sheets = [sheet for sheet in facility_sheets if sheet.get("active", True) and not sheet.get("superseded", False)]
    if len({sheet.get("id") for sheet in sheets}) != len(sheets):
        raise ValueError("Duplicate drawing IDs prevent an unambiguous matrix.")

    diagnostics, nodes = [], []
    excluded_ids = {}
    stats = {name: 0 for name in (
        "components", "direct_connections", "unread_labels", "duplicate_labels",
        "unreviewed_components", "unreviewed_connections", "excluded_connections",
        "excluded_components", "invalid_observations", "duplicate_connections",
        "accepted_connection_observations", "excluded_sheet_links",
    )}

    def diagnose(code, message, *, severity="warning", sheet=None, **fields):
        record = {"code": code, "severity": severity, "message": message, **fields}
        if sheet is not None:
            record["sheet_id"] = sheet["id"]
        diagnostics.append(record)

    for sheet in sheets:
        if not sheet.get("active", True) or sheet.get("superseded", False):
            diagnose("archived_revision", "This matrix describes the selected archived drawing revision.", sheet=sheet)
        components = sheet.get("components", ())
        counts = Counter(item.get("id") for item in components if isinstance(item, Mapping) and _identifier(item.get("id")))
        duplicate_ids = {value for value, count in counts.items() if count > 1}
        for value in sorted(duplicate_ids):
            diagnose("duplicate_component_id", f"Component ID {value} occurs more than once; those observations and their edges are excluded.",
                     severity="error", sheet=sheet, component_id=value, count=counts[value])
        for item in components:
            component_id = item.get("id") if isinstance(item, Mapping) else None
            key = sheet["id"], component_id
            if not _identifier(component_id):
                stats["excluded_components"] += 1
                stats["invalid_observations"] += 1
                diagnose("invalid_component_id", "A component has no valid source ID and cannot be represented in the matrix.", severity="error", sheet=sheet)
                continue
            if component_id in duplicate_ids:
                stats["excluded_components"] += 1
                stats["invalid_observations"] += 1
                excluded_ids[key] = "ambiguous component ID"
                continue
            if item.get("review_status") == "rejected":
                stats["excluded_components"] += 1
                excluded_ids[key] = "rejected equipment observation"
                diagnose("rejected_component", "Rejected equipment observation is excluded from the matrix.", sheet=sheet, component_id=component_id)
                continue
            typ = _type(item.get("type")) or "unknown"
            if typ in TOPOLOGY_TYPES:
                stats["excluded_components"] += 1
                excluded_ids[key] = "topology or non-equipment marking"
                diagnose("non_equipment_component", "A junction, continuation or other non-equipment marking is not an equipment matrix row; no incident paths are contracted.",
                         severity="info", sheet=sheet, component_id=component_id, type=typ)
                continue
            observed = item.get("tag", item.get("observed_tag"))
            if observed is not None and not isinstance(observed, str):
                diagnose("invalid_component_tag", "Equipment name is not text; its source ID is retained as an unresolved row.", severity="error", sheet=sheet, component_id=component_id)
                stats["invalid_observations"] += 1
                observed = None
            normalized = normalize_tag(observed)
            review = item.get("review_status", "unreviewed")
            node = {"index": len(nodes), "sheet_id": sheet["id"], "component_id": component_id,
                    "drawing_number": sheet.get("drawing_number", ""), "tag": observed or "",
                    "normalized_tag": normalized, "type": typ, "review_status": review}
            nodes.append(node)
            if normalized is None:
                stats["unread_labels"] += 1
                diagnose("unread_component_tag", "Equipment name is unread; its unique source ID remains in the matrix.", sheet=sheet, component_id=component_id)
            if typ == "unknown":
                diagnose("unknown_component_type", "Equipment type remains unknown.", sheet=sheet, component_id=component_id)
            if review != "confirmed":
                stats["unreviewed_components"] += 1

    tags = Counter(node["normalized_tag"] for node in nodes if node["normalized_tag"] is not None)
    used_labels = set()
    for node in nodes:
        tag = node["normalized_tag"]
        qualified = f"{node['sheet_id']}/{node['component_id']}"
        label = tag or f"unresolved:{qualified}"
        if tag and tags[tag] > 1:
            stats["duplicate_labels"] += 1
            label = f"{tag} [{qualified}]"
            diagnose("duplicate_component_tag", "This equipment name is repeated; the matrix keeps separate source IDs instead of merging equipment.",
                     sheet_id=node["sheet_id"], component_id=node["component_id"], tag=tag)
        base, suffix = label, 2
        while label in used_labels:
            label = f"{base} [{qualified}:{suffix}]"
            suffix += 1
        node["label"] = label
        used_labels.add(label)

    by_ref = {(node["sheet_id"], node["component_id"]): node for node in nodes}
    pairs = {}
    for sheet in sheets:
        for ordinal, connection in enumerate(sheet.get("connections", ())):
            if not isinstance(connection, Mapping):
                stats["excluded_connections"] += 1
                stats["invalid_observations"] += 1
                diagnose("invalid_connection", "A connection is not a structured observation and is excluded.", severity="error", sheet=sheet)
                continue
            connection_id = connection.get("id", f"connection-{ordinal + 1}")
            metadata = {"connection_id": str(connection_id)}
            source, target = connection.get("source"), connection.get("target")
            reason = code = None
            severity = "warning"
            if not _identifier(source) or not _identifier(target):
                code, reason, severity = "invalid_endpoint", "Connection endpoint IDs are invalid; the observation is excluded.", "error"
            elif source == target:
                code, reason, severity = "self_connection", "A self-connection is invalid; the matrix diagonal remains zero.", "error"
            elif connection.get("review_status") == "rejected":
                code, reason = "rejected_connection", "Rejected connection is excluded from direct adjacency."
            elif connection.get("kind") == "signal":
                code, reason, severity = "signal_connection", "Instrument signal is excluded from equipment process adjacency.", "info"
            elif connection.get("kind") not in DIRECT_KINDS:
                code, reason = "unclassified_connection", "Line kind is unknown or unsupported; no matrix edge is asserted."
            else:
                missing = [endpoint for endpoint in (source, target) if (sheet["id"], endpoint) not in by_ref]
                if missing:
                    known_excluded = [(sheet["id"], endpoint) in excluded_ids for endpoint in missing]
                    if all(known_excluded):
                        code, reason = "excluded_endpoint", "Connection touches excluded equipment or a non-equipment marking; no equipment-to-equipment shortcut is inferred."
                    else:
                        code, reason, severity = "missing_endpoint", "Connection references an absent equipment ID; the observation is excluded.", "error"
            if reason:
                stats["excluded_connections"] += 1
                if severity == "error":
                    stats["invalid_observations"] += 1
                diagnose(code, reason, severity=severity, sheet=sheet, source=source, target=target, **metadata)
                continue
            first, second = by_ref[(sheet["id"], source)], by_ref[(sheet["id"], target)]
            if (connection.get("require_identified_endpoints") and connection.get("review_status") != "confirmed"
                    and any(node["type"] == "unknown" and node["normalized_tag"] is None for node in (first, second))):
                stats["excluded_connections"] += 1
                diagnose("unidentified_equipment", "A traced line is held for review until both ends are identified as equipment.",
                         sheet=sheet, source=source, target=target, **metadata)
                continue
            if first["index"] > second["index"]:
                first, second = second, first
            pair = first["index"], second["index"]
            review = connection.get("review_status", "unreviewed")
            observation = {"sheet_id": sheet["id"], "connection_id": str(connection_id),
                           "source_component_id": source, "target_component_id": target,
                           "review_status": review, "kind": connection["kind"]}
            for field in ("path", "direction", "direction_evidence", "evidence", "uncertainty"):
                if field in connection:
                    observation[field] = deepcopy(connection[field])
            if pair in pairs:
                stats["duplicate_connections"] += 1
                diagnose("duplicate_connection", "Repeated, reversed or parallel records share one binary direct adjacency entry; all observations remain listed.",
                         severity="info", sheet=sheet, **metadata)
            else:
                pairs[pair] = {"source": _ref(first), "target": _ref(second), "source_index": first["index"],
                               "target_index": second["index"], "source_label": first["label"], "target_label": second["label"],
                               "kind": "direct_undirected", "observations": [], "review_status": "unreviewed"}
            pairs[pair]["observations"].append(observation)
            if review == "confirmed":
                pairs[pair]["review_status"] = "confirmed"
            else:
                stats["unreviewed_connections"] += 1
            stats["accepted_connection_observations"] += 1

    sheet_ids = {sheet["id"] for sheet in sheets}
    for link in state.get("links", ()):
        if not isinstance(link, Mapping):
            continue
        endpoints = [link.get(end) or {} for end in ("from", "to")]
        if link.get("facility_id") == facility_id and any(
            isinstance(ref, Mapping) and ref.get("sheet_id") in sheet_ids for ref in endpoints
        ):
            stats["excluded_sheet_links"] += 1
            diagnose("sheet_link_not_direct_equipment_edge", "Sheet-continuation links are not direct equipment edges in this matrix; no connector paths are contracted.",
                     severity="info", link_id=link.get("id"))

    values = [[0 for _ in nodes] for _ in nodes]
    for first, second in pairs:
        values[first][second] = values[second][first] = 1
    stats.update(components=len(nodes), direct_connections=len(pairs))
    warnings = [item["message"] for item in diagnostics if item["severity"] != "info"]
    if include_unreviewed_warnings and stats["unreviewed_components"]:
        warnings.append(f"{stats['unreviewed_components']} equipment observations still require review.")
    if include_unreviewed_warnings and stats["unreviewed_connections"]:
        warnings.append(f"{stats['unreviewed_connections']} included connection observations still require review.")
    return {"scope": {"kind": "sheet" if sheet_id is not None else "facility", "facility_id": facility_id,
                      "sheet_id": sheet_id, "sheet_ids": [sheet["id"] for sheet in sheets], "cross_sheet": False},
            "nodes": nodes, "order": [node["label"] for node in nodes], "matrix": values,
            "direct_edges": [pairs[pair] for pair in sorted(pairs)], "diagnostics": diagnostics,
            "warnings": list(dict.fromkeys(warnings)), "stats": stats,
            "convention": "Undirected immediate equipment adjacency in saved observation order. 1 is an included direct relation; 0 is no included direct relation, not proof of physical disconnection. No transitive closure, junction contraction or sheet-link contraction."}


def matrix_csv(result):
    """Return the same explicit label order on both axes as a CSV string.

    Formula-like names receive a leading apostrophe for spreadsheet safety. The
    exact source text and unsanitized display labels remain in the JSON result.
    """
    def safe_label(value):
        text = str(value)
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    labels = [safe_label(label) for label in result["order"]]
    writer.writerow(["Equipment", *labels])
    for label, row in zip(labels, result["matrix"]):
        writer.writerow([label, *row])
    return stream.getvalue()
