"""Facility-scoped piping traversal and reviewable isolation candidates."""
from __future__ import annotations

from collections import defaultdict, deque


DEFAULT_MANUAL_TYPES = (
    "manual valve", "manual isolation valve", "gate valve", "globe valve",
    "ball valve", "butterfly valve", "plug valve", "diaphragm valve",
    "needle valve", "stop valve", "isolation valve",
)
EXCLUDED_ACTUATION = ("check", "motor", "power", "solenoid", "pneumatic", "hydraulic", "automatic")
CONNECTOR_TYPES = {"connector", "off page connector", "line continuation"}


def _text(value):
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def _ref(key):
    return {"sheet_id": key[0], "component_id": key[1]}


def _key(ref):
    if not isinstance(ref, dict):
        return None
    sheet, component = ref.get("sheet_id"), ref.get("component_id")
    return (sheet, component) if isinstance(sheet, str) and isinstance(component, str) else None


def _append_unique(items, value):
    if value not in items:
        items.append(value)


def _facility_sheets(state, facility_id):
    return [sheet for sheet in state.get("sheets", ())
            if sheet.get("facility_id") == facility_id and sheet.get("active", True)
            and not sheet.get("superseded", False)]


def _graph(state, facility_id, include_signals=False):
    """Build a graph with explicit issue records at affected component nodes."""
    sheets = _facility_sheets(state, facility_id)
    nodes, adjacency = {}, defaultdict(list)
    issues = defaultdict(list)
    for sheet in sheets:
        for component in sheet.get("components", ()):
            key = sheet["id"], component["id"]
            if key in nodes:
                raise ValueError(f"Duplicate component ID {component['id']} on sheet {sheet['id']}.")
            nodes[key] = {**component, **_ref(key), "sheet_title": sheet.get("title", sheet["id"]),
                          "drawing_number": sheet.get("drawing_number", ""),
                          "revision": sheet.get("revision", ""), "facility_id": facility_id}
    edges = []

    def add_edge(first, second, edge):
        if first not in nodes or second not in nodes:
            for key in (first, second):
                if key in nodes:
                    issues[key].append({"code": "missing_endpoint", "edge_id": edge["id"],
                                        "reason": "A connection endpoint is absent from the current facility graph."})
            return
        if first == second:
            issues[first].append({"code": "self_connection", "edge_id": edge["id"],
                                  "reason": "A connection points back to the same component."})
            return
        record = {**edge, "from": _ref(first), "to": _ref(second)}
        index = len(edges)
        edges.append(record)
        adjacency[first].append((second, index))
        adjacency[second].append((first, index))

    for sheet in sheets:
        for index, connection in enumerate(sheet.get("connections", ())):
            kind = connection.get("kind", "pipe")
            if kind == "signal" and not include_signals:
                continue
            if connection.get("review_status") == "rejected":
                for ident in (connection.get("source"), connection.get("target")):
                    key = sheet["id"], ident
                    if key in nodes:
                        issues[key].append({"code": "rejected_connection",
                                            "edge_id": str(connection.get("id", f"edge-{index}")),
                                            "reason": "The recorded connection was rejected and is not traversed."})
                continue
            if kind not in {"pipe", "signal", "direct_undirected"}:
                for ident in (connection.get("source"), connection.get("target")):
                    key = sheet["id"], ident
                    if key in nodes:
                        issues[key].append({"code": "unsupported_connection", "reason": f"Unsupported line kind {kind}."})
                continue
            first = sheet["id"], connection.get("source")
            second = sheet["id"], connection.get("target")
            add_edge(first, second, {**connection, "id": str(connection.get("id", f"edge-{index}")),
                                     "sheet_id": sheet["id"], "kind": "signal" if kind == "signal" else "pipe",
                                     "cross_sheet": False})

    confirmed_endpoints = set()
    for index, link in enumerate(state.get("links", ())):
        first, second = _key(link.get("from")), _key(link.get("to"))
        relevant = [key for key in (first, second) if key in nodes]
        if not relevant:
            continue
        status = link.get("status", "suggested")
        if status == "rejected":
            continue
        link_id = str(link.get("id", f"link-{index}"))
        if link.get("facility_id", facility_id) != facility_id or first not in nodes or second not in nodes:
            for key in relevant:
                issues[key].append({"code": "invalid_sheet_link", "link_id": link_id,
                                    "reason": "Sheet link leaves the facility or refers to an unavailable endpoint."})
            continue
        if status != "confirmed":
            for key in relevant:
                issues[key].append({"code": "unconfirmed_sheet_link", "link_id": link_id,
                                    "reason": "Suggested sheet link needs confirmation against both drawings."})
            continue
        kind = link.get("kind", "pipe")
        if (first[0] == second[0] or kind not in {"pipe", "signal"}
                or any(_text(nodes[key].get("type")) not in CONNECTOR_TYPES for key in (first, second))):
            for key in relevant:
                issues[key].append({"code": "invalid_sheet_link", "link_id": link_id,
                                    "reason": "Sheet continuation requires two connectors on different sheets and a supported line kind."})
            continue
        if kind == "signal" and not include_signals:
            continue
        add_edge(first, second, {"id": link_id, "kind": kind, "cross_sheet": True,
                                 "review_status": "confirmed", "link_status": "confirmed"})
        confirmed_endpoints.update((first, second))

    for key, node in nodes.items():
        if _text(node.get("type")) in CONNECTOR_TYPES:
            if key not in confirmed_endpoints:
                issues[key].append({"code": "missing_sheet_link", "reason": "Off-page connector has no confirmed continuation."})
    return nodes, adjacency, edges, issues


def _start(nodes, facility_id, sheet_id, component_id):
    key = sheet_id, component_id
    if key not in nodes:
        raise ValueError(f"Component {component_id} on sheet {sheet_id} is not in facility {facility_id}.")
    return key


def _warning(node, message):
    label = node.get("tag") or node.get("label") or node["component_id"]
    return f"{node['sheet_title']} / {label}: {message}"


def trace_network(state, facility_id, sheet_id, component_id, include_signals=False):
    """Follow process connections and confirmed continuations within one facility."""
    nodes, adjacency, edges, issues = _graph(state, facility_id, include_signals)
    start = _start(nodes, facility_id, sheet_id, component_id)
    visited, used_edges = set(), set()
    queue = deque([start])
    warnings = []
    while queue:
        key = queue.popleft()
        if key in visited:
            continue
        visited.add(key)
        for issue in issues.get(key, ()):
            _append_unique(warnings, _warning(nodes[key], issue["reason"]))
        for neighbor, index in adjacency[key]:
            used_edges.add(index)
            if neighbor not in visited:
                queue.append(neighbor)
    return {"facility_id": facility_id, "start": _ref(start), "include_signals": include_signals,
            "nodes": [nodes[key] for key in sorted(visited)],
            "edges": [edges[index] for index in sorted(used_edges)], "warnings": warnings}


def _rules(state):
    config = state.get("isolation_rules", {})
    configured = config.get("manual_valve_types", DEFAULT_MANUAL_TYPES)
    if not isinstance(configured, (list, tuple)) or any(not isinstance(item, str) for item in configured):
        raise ValueError("isolation_rules.manual_valve_types must be a list of valve type names.")
    return {"name": "Conservative engineering review candidate", "version": "1",
            "manual_valve_types": sorted({_text(item) for item in configured}),
            "require_confirmed_components": True, "require_explicit_isolation_credit": True,
            "exclude_automatic_and_check_valves": True, "confirmed_sheet_links_only": True,
            "process_lines_only": True, "connectivity": "undirected",
            "review": "Candidate boundary requires the site's engineering and clearance review."}


def _classification(node, rules):
    typ = _text(node.get("type"))
    actuation = _text(node.get("actuation"))
    role = _text(node.get("role"))
    classification = {"type": typ, "role": role}
    type_words = typ.split()
    if role in {"drain", "vent", "relief"} or any(word in type_words for word in ("drain", "vent", "relief")):
        classification["special"] = role if role in {"drain", "vent", "relief"} else next(
            word for word in ("drain", "vent", "relief") if word in type_words)
    valve = "valve" in typ or node.get("is_valve", False)
    excluded = next((term for term in EXCLUDED_ACTUATION if term in typ or term in actuation), None)
    eligible_type = typ in rules["manual_valve_types"] or (valve and actuation == "manual")
    credit = node.get("isolation_credit", "unverified")
    reviewed = node.get("review_status") == "confirmed"
    if valve or excluded:
        if excluded:
            classification["no_credit"] = f"{excluded.capitalize()} valve is excluded by the configured default rules."
        elif credit == "excluded":
            classification["no_credit"] = "Equipment is explicitly excluded from isolation credit."
        elif not reviewed:
            classification["no_credit"] = "Component type and isolation credit await confirmation."
        elif not eligible_type:
            classification["no_credit"] = "Valve type or manual actuation has not been confirmed as eligible."
        elif credit != "eligible":
            classification["no_credit"] = "No explicit isolation credit is recorded."
        else:
            classification["closes"] = True
    return classification


def scope_isolation(state, facility_id, sheet_id, component_id):
    """Find the first eligible manual valves on each observed process path."""
    nodes, adjacency, edges, issues = _graph(state, facility_id)
    start = _start(nodes, facility_id, sheet_id, component_id)
    rules = _rules(state)
    result = {"status": "candidate", "facility_id": facility_id, "target": nodes[start],
              "closures": [], "drains": [], "vents": [], "relief": [], "not_credited": [],
              "open_ends": [], "warnings": [], "nodes": [], "edges": [], "rules": rules}
    queue, visited, used_edges = deque([start]), set(), set()
    blocked = set()
    while queue:
        key = queue.popleft()
        if key in visited:
            continue
        visited.add(key)
        node = nodes[key]
        label = node.get("tag") or node.get("label") or node["component_id"]
        item = {**_ref(key), "tag": label, "type": node.get("type", "unknown"),
                "normal_position": node.get("normal_position", "unknown"), "grid": node.get("grid")}
        for issue in issues.get(key, ()):
            result["open_ends"].append({**item, **issue})
        category = _classification(node, rules)
        special = category.get("special")
        if special:
            result[{"drain": "drains", "vent": "vents", "relief": "relief"}[special]].append(item)
        if key != start and category.get("closes"):
            reason = "Confirmed manual valve with explicit isolation credit."
            if node.get("normal_position") == "closed":
                reason += " Verify the drawn normally closed position in the field."
            elif node.get("normal_position", "unknown") == "unknown":
                reason += " Normal position is unrecorded and requires verification."
            result["closures"].append({**item, "index": len(result["closures"]) + 1, "reason": reason})
            blocked.add(key)
            continue
        if category.get("no_credit"):
            result["not_credited"].append({**item, "reason": category["no_credit"]})
        if category["type"] in {"", "unknown"}:
            _append_unique(result["warnings"], _warning(node, "Equipment type is unconfirmed."))
        if node.get("review_status") != "confirmed":
            _append_unique(result["warnings"], _warning(node, "Component observation awaits review."))
        links = adjacency[key]
        neighbors = {neighbor for neighbor, _ in links}
        if category["role"] in {"source", "open end", "external source"} or node.get("open_end"):
            result["open_ends"].append({**item, "code": "explicit_open_end",
                                        "reason": "The drawing records an external source or open process end."})
        elif len(neighbors) <= 1 and key != start:
            reason = "Process path terminates without a credited isolation valve."
            result["open_ends"].append({**item, "code": "unbounded_terminal", "reason": reason})
        elif not links:
            result["open_ends"].append({**item, "code": "disconnected_target",
                                        "reason": "No process connections are recorded for the target."})
        for neighbor, index in links:
            used_edges.add(index)
            edge = edges[index]
            if edge.get("review_status") != "confirmed":
                _append_unique(result["warnings"], f"Connection {edge['id']} requires confirmation against the drawing.")
            if neighbor not in visited:
                queue.append(neighbor)
    if not blocked:
        _append_unique(result["warnings"], "No credited manual boundary valve was found on the observed process network.")
    if result["open_ends"] or result["warnings"]:
        result["status"] = "incomplete"
    result["nodes"] = [nodes[key] for key in sorted(visited)]
    result["edges"] = [edges[index] for index in sorted(used_edges)]
    return result


def search(state, facility_id, query):
    """Search drawing metadata and component tags or types in one facility."""
    needle = _text(query)
    result = {"sheets": [], "components": [], "connectors": []}
    for sheet in _facility_sheets(state, facility_id):
        haystack = _text(" ".join(str(sheet.get(field, "")) for field in
                                 ("title", "drawing_number", "sheet_number", "revision", "filename")))
        if needle in haystack:
            result["sheets"].append({key: sheet.get(key) for key in
                                     ("id", "facility_id", "title", "drawing_number", "sheet_number", "revision")})
        for component in sheet.get("components", ()):
            haystack = _text(" ".join(str(component.get(field, "")) for field in ("tag", "label", "type", "id")))
            if needle in haystack:
                item = {**component, "sheet_id": sheet["id"], "component_id": component["id"],
                        "sheet_title": sheet.get("title", ""), "facility_id": facility_id}
                result["components"].append(item)
                if _text(component.get("type")) in CONNECTOR_TYPES:
                    result["connectors"].append(item)
    return result
