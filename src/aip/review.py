"""Build a review queue from actionable drawing observations."""
from collections import Counter

from .core import normalize_tag
from .v2.matrix import TOPOLOGY_TYPES


_DIRECTION_ISSUES = {
    "ambiguous": "Arrow cannot be assigned to one connection",
    "conflict": "Visible arrows disagree",
    "unsupported": "Proposed direction lacks matching arrow evidence",
}
_LEGACY_DIRECTION_SUFFIX = ": The proposed direction lacked matching arrow evidence."


def _equipment(component):
    kind = " ".join((component.get("type") or "").lower().replace("-", " ").replace("_", " ").split())
    return kind not in TOPOLOGY_TYPES


def _low_model_confidence(record):
    confidence = record.get("confidence")
    return type(confidence) in (float, int) and 0 <= confidence < 0.5


def _tag(component):
    return normalize_tag(component.get("tag", component.get("observed_tag")))


def review_queue(trace):
    """Return editable issues, including those from traces saved before this feature."""
    queue = []
    components = [component for component in trace.get("components", ())
                  if component.get("review_status") != "rejected" and _equipment(component)]
    tags = Counter(tag for component in components if (tag := _tag(component)))
    for component in components:
        if component.get("review_status") == "confirmed":
            continue
        reasons = []
        tag = _tag(component)
        if tag is None:
            reasons.append("Equipment name unread")
        elif tags[tag] > 1:
            reasons.append("Equipment name is repeated on this drawing")
        elif component.get("label_source") == "chatgpt_crop":
            reasons.append("Check name transcribed from a drawing crop")
        if (component.get("type") or "").strip().lower() in ("", "unknown"):
            reasons.append("Equipment type unknown")
        if _low_model_confidence(component):
            reasons.append("Model reported low confidence")
        if reasons:
            queue.append({"kind": "component", "id": component["id"], "reason": " · ".join(reasons)})

    # Earlier vision traces recorded failed direction claims only in their diagnostic
    # strings. An unaligned arrow retained its proposed association for inspection.
    legacy_claims = {message.removesuffix(_LEGACY_DIRECTION_SUFFIX)
                     for message in trace.get("uncertainties", ())
                     if message.endswith(_LEGACY_DIRECTION_SUFFIX)}
    unaligned_arrows = {arrow["proposed_connection_id"] for arrow in trace.get("arrows", ())
                        if arrow.get("proposed_connection_id")}
    for edge in trace.get("connections", ()):
        if edge.get("review_status") in {"confirmed", "rejected"}:
            continue
        ident = edge["id"]
        basis = (edge.get("direction_evidence") or {}).get("basis")
        reasons = []
        if basis in _DIRECTION_ISSUES:
            reasons.append(_DIRECTION_ISSUES[basis])
        elif edge.get("direction", "unknown") == "unknown":
            if ident in legacy_claims:
                reasons.append(_DIRECTION_ISSUES["unsupported"])
            elif ident in unaligned_arrows:
                reasons.append("Proposed arrow could not be aligned with the connection")
        if edge.get("kind") in {None, "", "unknown"}:
            reasons.append("Connection type unknown")
        if _low_model_confidence(edge):
            reasons.append("Model reported low confidence")
        if reasons:
            queue.append({"kind": "connection", "id": ident, "reason": " · ".join(reasons)})
    return queue
