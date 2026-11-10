"""Small versioned observation contract and truth-independent normalization."""
from __future__ import annotations

import copy
import math
import re
import unicodedata
from datetime import datetime
from typing import Any

SCHEMA_VERSION = "1.0.0"
UNRESOLVED_TAGS = {"", "UNKNOWN", "UNREADABLE", "UNRESOLVED", "N/A", "?"}
COORDINATE_CONVENTION = {
    "origin": "top-left", "units": "original image pixels",
    "axes": "x right, y down", "bbox_format": ["xmin", "ymin", "xmax", "ymax"],
    "bounds": "minimum inclusive, maximum exclusive",
    "scope": "symbol only; excludes tag text and external pipes",
}


class ValidationError(ValueError):
    """Actionable validation diagnostics; callers must preserve the raw input."""

    def __init__(self, errors):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


def normalize_tag(value: str | None) -> str | None:
    """NFKC, uppercase, trim, normalize dashes and spaces beside separators.

    No insertion of missing punctuation, OCR repair, or reference-based matching.
    The observed text is retained separately, including unresolved placeholders.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("observed_tag must be a string or null")
    text = unicodedata.normalize("NFKC", value).strip().upper()
    text = text.translate(str.maketrans({c: "-" for c in "‐‑‒–—−﹘﹣－"}))
    text = re.sub(r"\s*([-/:])\s*", r"\1", text)
    return None if text in UNRESOLVED_TAGS else text


def _object(value, location, errors):
    if not isinstance(value, dict):
        errors.append(f"{location} must be a JSON object")
        return {}
    return value


def _array(value, location, errors):
    if not isinstance(value, list):
        errors.append(f"{location} must be a JSON array")
        return []
    return value


def _text_or_null(value, location, errors):
    if value is not None and not isinstance(value, str):
        errors.append(f"{location} must be text or null")


def _bbox(value, location, width, height, errors):
    if value is None:
        return
    if not isinstance(value, list) or len(value) != 4:
        errors.append(f"{location} must be [xmin,ymin,xmax,ymax] or null")
        return
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        errors.append(f"{location} must contain four finite numbers")
        return
    x0, y0, x1, y1 = value
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        errors.append(f"{location} must enclose positive area within original image {width}x{height}")


def _metadata(drawing, run, errors):
    drawing = _object(drawing, "drawing", errors)
    run = _object(run, "run", errors)
    for name in ("drawing_id", "filename", "sha256"):
        if not isinstance(drawing.get(name), str) or not drawing[name].strip():
            errors.append(f"drawing.{name} must be nonempty text")
    if isinstance(drawing.get("sha256"), str) and not re.fullmatch(r"[0-9a-fA-F]{64}", drawing["sha256"]):
        errors.append("drawing.sha256 must contain 64 hexadecimal characters")
    if "source_sha256" in drawing and (not isinstance(drawing["source_sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", drawing["source_sha256"])):
        errors.append("drawing.source_sha256 must contain 64 hexadecimal characters when supplied")
    for name in ("width", "height"):
        val = drawing.get(name)
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            errors.append(f"drawing.{name} must be a positive integer")
    for name in ("run_id", "timestamp", "timezone", "method", "access_route", "raw_response_location", "prompt_version", "prompt_sha256"):
        if not isinstance(run.get(name), str) or not run[name].strip():
            errors.append(f"run.{name} must be nonempty text")
    try:
        timestamp = datetime.fromisoformat(run.get("timestamp", ""))
        if timestamp.utcoffset() is None:
            errors.append("run.timestamp must include a UTC offset")
    except (ValueError, TypeError):
        errors.append("run.timestamp must be an ISO-8601 timestamp with UTC offset")
    if isinstance(run.get("prompt_sha256"), str) and not re.fullmatch(r"[0-9a-fA-F]{64}", run["prompt_sha256"]):
        errors.append("run.prompt_sha256 must contain 64 hexadecimal characters")
    for name in ("provider", "model"):
        if name not in run or (run[name] is not None and (not isinstance(run[name], str) or not run[name].strip())):
            errors.append(f"run.{name} must be recorded as nonempty text or null when unavailable")
    if not isinstance(run.get("settings"), dict):
        errors.append("run.settings must be an object")
    if not isinstance(run.get("manual_stages"), list) or any(not isinstance(v, str) for v in run.get("manual_stages", [])):
        errors.append("run.manual_stages must be an array of strings")
    duration = run.get("duration_seconds")
    if ("duration_seconds" not in run or (duration is not None and
        (isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0))):
        errors.append("run.duration_seconds must be nonnegative finite seconds or null when unavailable")
    code = run.get("code")
    if not isinstance(code, dict):
        errors.append("run.code must record revision and dirty_state, using null when unavailable")
    else:
        for name in ("revision", "dirty_state"):
            if name not in code or (code[name] is not None and not isinstance(code[name], str)):
                errors.append(f"run.code.{name} must be text or null")
    return drawing, run


def normalize_response(raw: dict, drawing: dict, run: dict) -> dict:
    """Convert observed JSON to the canonical schema without consulting truth."""
    errors: list[str] = []
    warnings: list[str] = []
    raw = _object(raw, "response", errors)
    drawing, run = _metadata(drawing, run, errors)
    if errors:
        raise ValidationError(errors)
    width, height = drawing["width"], drawing["height"]
    reported = _object(raw.get("image", {}), "image", errors)
    for key, expected in (("width", width), ("height", height)):
        value = reported.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value != expected):
            errors.append(f"image.{key}={value!r} disagrees with supplied original image {expected}; do not rescale boxes silently")
    components = []
    seen_ids, seen_tags = set(), set()
    for i, item in enumerate(_array(raw.get("components"), "components", errors)):
        loc = f"components[{i}]"
        item = _object(item, loc, errors)
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            errors.append(f"{loc}.id must be nonempty text")
        elif item_id in seen_ids:
            errors.append(f"{loc}.id duplicates observation ID {item_id!r}")
        else:
            seen_ids.add(item_id)
        if "observed_tag" not in item:
            errors.append(f"{loc}.observed_tag is required; use null for an unreadable tag")
        tag = item.get("observed_tag")
        normalized = None
        try:
            normalized = normalize_tag(tag)
        except ValidationError as exc:
            errors.extend(f"{loc}.{message}" for message in exc.errors)
        if normalized is not None:
            if normalized in seen_tags:
                errors.append(f"{loc}.observed_tag duplicates normalized tag {normalized!r}; resolve ambiguous identities explicitly")
            seen_tags.add(normalized)
        else:
            warnings.append(f"{loc} has unresolved tag; retained and unscored by tag")
        typ = item.get("type", "unknown")
        if not isinstance(typ, str):
            errors.append(f"{loc}.type must be a string; use 'unknown' if unresolved")
            typ = "unknown"
        typ = typ.strip().lower() or "unknown"
        for key in ("evidence", "uncertainty"):
            _text_or_null(item.get(key), f"{loc}.{key}", errors)
        _bbox(item.get("bbox"), f"{loc}.bbox", width, height, errors)
        components.append({"id": item_id, "observed_tag": tag, "normalized_tag": normalized,
                           "type": typ, "bbox": copy.deepcopy(item.get("bbox")),
                           "evidence": item.get("evidence"), "uncertainty": item.get("uncertainty"),
                           "review_status": "pending human review"})
    connections = []
    seen_pairs = set()
    for i, item in enumerate(_array(raw.get("connections"), "connections", errors)):
        loc = f"connections[{i}]"
        item = _object(item, loc, errors)
        source, target = item.get("source"), item.get("target")
        endpoints_valid = True
        for key, value in (("source", source), ("target", target)):
            if not isinstance(value, str) or value not in seen_ids:
                errors.append(f"{loc}.{key}={value!r} does not name an observed component ID")
                endpoints_valid = False
        if source == target:
            errors.append(f"{loc} is a prohibited self-loop")
            endpoints_valid = False
        if item.get("kind") != "direct_undirected":
            errors.append(f"{loc}.kind must be 'direct_undirected'; do not mix instrument signals or directed links")
        for key in ("evidence", "uncertainty"):
            _text_or_null(item.get(key), f"{loc}.{key}", errors)
        if endpoints_valid:
            pair = tuple(sorted((source, target)))
            if pair in seen_pairs:
                warnings.append(f"{loc} duplicates/reverses {pair[0]}--{pair[1]}; one unordered edge retained; raw response preserves every observation")
                continue
            seen_pairs.add(pair)
            connections.append({"source": pair[0], "target": pair[1], "kind": "direct_undirected",
                                "evidence": item.get("evidence"), "uncertainty": item.get("uncertainty"),
                                "review_status": "pending human review"})
    uncertainties = _array(raw.get("uncertainties", []), "uncertainties", errors)
    if any(not isinstance(v, str) for v in uncertainties):
        errors.append("uncertainties must contain only strings")
    if errors:
        raise ValidationError(errors)
    metadata = copy.deepcopy(drawing)
    metadata.setdefault("coordinate_convention", COORDINATE_CONVENTION)
    return {"schema_version": SCHEMA_VERSION, "drawing": metadata, "run": copy.deepcopy(run),
            "components": components, "connections": connections,
            "uncertainties": copy.deepcopy(uncertainties), "warnings": warnings}


def validate_document(document: dict) -> dict:
    """Validate canonical data, including consistency of normalized fields.

    A canonical document with duplicate pairs is rejected, since normalization is
    the explicit stage that deduplicates and records that action.
    """
    errors = []
    if not isinstance(document, dict):
        raise ValidationError("document must be a JSON object")
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    drawing = document.get("drawing")
    if isinstance(drawing, dict):
        convention = drawing.get("coordinate_convention")
        if not isinstance(convention, (dict, str)):
            errors.append("drawing.coordinate_convention must document top-left original-pixel half-open boxes")
        elif isinstance(convention, dict) and convention.get("origin") != "top-left":
            errors.append("drawing.coordinate_convention.origin must be top-left")
        elif isinstance(convention, str) and "top-left" not in convention.lower():
            errors.append("drawing.coordinate_convention must use the top-left origin")
    allowed_top = {"schema_version", "drawing", "run", "components", "connections", "uncertainties", "warnings"}
    if set(document) - allowed_top:
        errors.append("canonical document has unsupported fields: " + ", ".join(sorted(set(document) - allowed_top)))
    raw = {"image": {}, "components": document.get("components"),
           "connections": document.get("connections"), "uncertainties": document.get("uncertainties")}
    try:
        checked = normalize_response(raw, document.get("drawing"), document.get("run"))
    except ValidationError as exc:
        errors.extend(exc.errors)
        raise ValidationError(errors) from exc
    if len(checked["connections"]) != len(document["connections"]):
        errors.append("canonical connections contain duplicate unordered pairs; rerun normalization and preserve warnings")
    for i, (actual, normalized) in enumerate(zip(document["components"], checked["components"])):
        allowed = {"id", "observed_tag", "normalized_tag", "type", "bbox", "evidence", "uncertainty", "review_status"}
        if set(actual) - allowed or allowed - set(actual):
            errors.append(f"components[{i}] must contain exactly the canonical component fields")
        if "normalized_tag" not in actual or actual["normalized_tag"] != normalized["normalized_tag"]:
            errors.append(f"components[{i}].normalized_tag disagrees with truth-independent normalization")
        if actual.get("type") != normalized["type"]:
            errors.append(f"components[{i}].type must be normalized lowercase text")
        if not isinstance(actual.get("review_status"), str) or not actual["review_status"].strip():
            errors.append(f"components[{i}].review_status is required")
    for i, edge in enumerate(document["connections"]):
        allowed = {"source", "target", "kind", "evidence", "uncertainty", "review_status"}
        if set(edge) != allowed:
            errors.append(f"connections[{i}] must contain exactly the canonical connection fields")
        if not isinstance(edge.get("review_status"), str) or not edge["review_status"].strip():
            errors.append(f"connections[{i}].review_status is required")
    if not isinstance(document.get("warnings"), list) or any(not isinstance(x, str) for x in document.get("warnings", [])):
        errors.append("warnings must be an array of strings")
    if errors:
        raise ValidationError(errors)
    return document
