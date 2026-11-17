"""Transactional local state and correction history for the drawing workbench."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import secrets
import sqlite3
import threading
import uuid
from urllib.parse import urlsplit

from .grid import grid_settings


def now():
    return datetime.now(timezone.utc).isoformat()


def ident(prefix):
    return prefix + "-" + uuid.uuid4().hex[:12]


def find(items, object_id):
    return next((item for item in items if item["id"] == object_id), None)


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "workbench.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)")
        row = self.db.execute("SELECT document FROM state WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else {
            "version": 2, "facilities": [{"id": "facility-local", "name": "Research facility"}],
            "sheets": [], "links": [], "jobs": [], "audit": [],
            "settings": {"actor": "Local reviewer", "provider": "local", "model": "", "base_url": "",
                         "use_geometry_reference": True},
            "isolation_rules": {"manual_valve_types": ["manual valve", "gate valve", "ball valve",
                                                       "globe valve", "isolation valve"]},
            "api_key": secrets.token_urlsafe(32),
        }
        for job in state["jobs"]:
            if job["status"] in {"queued", "running"}:
                job.update(status="interrupted", error="The application stopped before this job completed.")
        self._save(state)

    def _save(self, state):
        self.db.execute("INSERT OR REPLACE INTO state(id, document) VALUES (1, ?)",
                        (json.dumps(state, ensure_ascii=False, allow_nan=False),))
        self.db.commit()

    def snapshot(self, private=False):
        with self.lock:
            state = json.loads(self.db.execute("SELECT document FROM state WHERE id=1").fetchone()[0])
        if not private:
            state.pop("api_key", None)
        return state

    def mutate(self, callback, action=None, actor=None, details=None):
        with self.lock:
            state = self.snapshot(private=True)
            result = callback(state)
            if action:
                state["audit"].append({"id": ident("event"), "time": now(), "actor": actor or state["settings"]["actor"],
                                       "action": action, "details": details or {}})
            self._save(state)
            return deepcopy(result)

    def update_settings(self, patch):
        allowed = {"actor", "provider", "model", "base_url", "use_geometry_reference"}
        if set(patch) - allowed:
            raise ValueError("Settings contain unsupported fields. API credentials belong in environment variables.")
        def update(state):
            state["settings"].update(patch)
            if state["settings"]["provider"] not in {"local", "openai", "anthropic", "compatible"}:
                raise ValueError("Unknown AI provider.")
            for field in ("actor", "model", "base_url"):
                if not isinstance(state["settings"][field], str) or len(state["settings"][field]) > 1000:
                    raise ValueError("Invalid setting.")
            if not isinstance(state["settings"]["use_geometry_reference"], bool):
                raise ValueError("Reference matching must be true or false.")
            base_url = state["settings"]["base_url"].strip()
            if base_url:
                parsed = urlsplit(base_url)
                if parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError("Gateway URL must not contain credentials, a query or a fragment.")
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ValueError("Gateway URL must be an HTTP or HTTPS address.")
            state["settings"]["base_url"] = base_url
            return state["settings"]
        return self.mutate(update, "settings.updated")

    def add_facility(self, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError("Enter a facility name.")
        def add(state):
            facility = {"id": ident("facility"), "name": name.strip()}
            state["facilities"].append(facility)
            return facility
        return self.mutate(add, "facility.created", details={"name": name})

    def add_sheet(self, sheet):
        def add(state):
            if not find(state["facilities"], sheet["facility_id"]):
                raise ValueError("Choose an existing facility.")
            existing = next((s for s in state["sheets"] if s["facility_id"] == sheet["facility_id"]
                             and s.get("source_sha256") == sheet.get("source_sha256")
                             and s.get("source_page") == sheet.get("source_page")
                             and s.get("source_sha256")), None)
            if existing:
                return {"sheet": existing, "duplicate": True}
            item = deepcopy(sheet)
            item.update(version=1, created_at=now(), updated_at=now(), active=True)
            item.setdefault("corrections", [])
            item.setdefault("analysis", {"components": deepcopy(item.get("components", [])),
                                         "connections": deepcopy(item.get("connections", []))})
            state["sheets"].append(item)
            return {"sheet": item, "duplicate": False}
        return self.mutate(add, "sheet.imported", details={"sheet_id": sheet["id"], "title": sheet["title"]})

    def sheet(self, sheet_id):
        item = find(self.snapshot()["sheets"], sheet_id)
        if item is None:
            raise ValueError("Drawing was not found.")
        return item

    def update_sheet(self, sheet_id, patch, expected_version=None):
        allowed = {"title", "drawing_number", "sheet_number", "revision", "active", "grid", "source_metadata"}
        if set(patch) - allowed:
            raise ValueError("Unsupported drawing field.")
        if "grid" in patch:
            patch = {**patch, "grid": grid_settings(patch["grid"])}
        if "active" in patch and not isinstance(patch["active"], bool):
            raise ValueError("Current revision must be true or false.")
        if "source_metadata" in patch and not isinstance(patch["source_metadata"], dict):
            raise ValueError("Source metadata must be an object.")
        def edit(state):
            sheet = self._sheet_for_edit(state, sheet_id, expected_version)
            for field in ("title", "drawing_number", "sheet_number", "revision"):
                if field in patch and (not isinstance(patch[field], str) or len(patch[field]) > 500):
                    raise ValueError("Drawing metadata must be text.")
            sheet.update(deepcopy(patch))
            self._touch(sheet)
            return sheet
        return self.mutate(edit, "sheet.updated", details={"sheet_id": sheet_id, "fields": list(patch)})

    @staticmethod
    def _sheet_for_edit(state, sheet_id, expected_version=None):
        sheet = find(state["sheets"], sheet_id)
        if sheet is None:
            raise ValueError("Drawing was not found.")
        if expected_version is not None and expected_version != sheet["version"]:
            raise Conflict("This drawing changed in another window. Refresh before saving.")
        return sheet

    @staticmethod
    def _touch(sheet):
        sheet["version"] += 1
        sheet["updated_at"] = now()

    def edit_item(self, sheet_id, collection, item=None, delete_id=None, expected_version=None):
        if collection not in {"components", "connections"}:
            raise ValueError("Unsupported drawing item.")
        def edit(state):
            sheet = self._sheet_for_edit(state, sheet_id, expected_version)
            object_id = delete_id or (item or {}).get("id") or ident("component" if collection == "components" else "edge")
            existing = find(sheet[collection], object_id)
            if delete_id:
                if existing is None:
                    raise ValueError("Item was not found.")
                sheet[collection].remove(existing)
                if collection == "components":
                    sheet["connections"] = [edge for edge in sheet["connections"]
                                             if object_id not in (edge["source"], edge["target"])]
                    state["links"] = [link for link in state["links"] if all(
                        link[end] != {"sheet_id": sheet_id, "component_id": object_id} for end in ("from", "to"))]
                change = {"collection": collection, "id": object_id, "deleted": True}
            else:
                record = {**(existing or {}), **deepcopy(item), "id": object_id}
                self._validate_item(record, sheet, collection)
                if existing:
                    sheet[collection][sheet[collection].index(existing)] = record
                else:
                    sheet[collection].append(record)
                change = {"collection": collection, "id": object_id, "record": record}
            sheet["corrections"] = [entry for entry in sheet["corrections"]
                                     if not (entry["collection"] == collection and entry["id"] == object_id)]
            change.update(time=now(), actor=state["settings"]["actor"])
            baseline = find(sheet.get("analysis", {}).get(collection, []), object_id)
            change["baseline"] = deepcopy(baseline)
            sheet["corrections"].append(change)
            self._touch(sheet)
            return {"sheet": sheet, "item_id": object_id}
        return self.mutate(edit, "item.deleted" if delete_id else "item.corrected",
                           details={"sheet_id": sheet_id, "collection": collection, "item_id": delete_id or (item or {}).get("id")})

    @staticmethod
    def _validate_item(item, sheet, collection):
        if not isinstance(item.get("id"), str) or not item["id"] or len(item["id"]) > 200:
            raise ValueError("Item ID must be short text.")
        if collection == "components":
            box = item.get("bbox")
            if (not isinstance(box, list) or len(box) != 4 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in box) or
                not (0 <= box[0] < box[2] <= sheet["width"] and 0 <= box[1] < box[3] <= sheet["height"])):
                raise ValueError("Draw a positive symbol box inside the drawing.")
            for field in ("tag", "type"):
                if not isinstance(item.get(field, ""), str) or len(item.get(field, "")) > 250:
                    raise ValueError("Equipment label and type must be short text.")
            if not item.get("type", "").strip():
                item["type"] = "unknown"
            item.setdefault("normal_position", "unknown")
            item.setdefault("isolation_credit", "unverified")
            item.setdefault("review_status", "unreviewed")
            if item["normal_position"] not in {"open", "closed", "unknown"}:
                raise ValueError("Normal position must be open, closed or unknown.")
            if item["isolation_credit"] not in {"eligible", "excluded", "unverified"}:
                raise ValueError("Select the isolation credit status.")
            if item["review_status"] not in {"confirmed", "unreviewed", "rejected"}:
                raise ValueError("Select the review status.")
        else:
            ids = {component["id"] for component in sheet["components"]}
            if item.get("source") not in ids or item.get("target") not in ids or item["source"] == item["target"]:
                raise ValueError("A connection must join two different equipment items on this sheet.")
            if item.get("kind") not in {"pipe", "direct_undirected", "signal", "unknown"}:
                raise ValueError("Choose an equipment connection, pipe, signal or unknown line.")
            path = item.get("path")
            if path is not None and (not isinstance(path, list) or len(path) < 2 or any(
                not isinstance(p, list) or len(p) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int,float)) for v in p) or
                not (0 <= p[0] <= sheet["width"] and 0 <= p[1] <= sheet["height"]) for p in path)):
                raise ValueError("The connection path must lie inside the drawing.")
            item.setdefault("review_status", "unreviewed")
            if item["review_status"] not in {"confirmed", "unreviewed", "rejected"}:
                raise ValueError("Select the connection review status.")
            item.setdefault("direction", "unknown")
            if item["direction"] not in {"source_to_target", "target_to_source", "bidirectional", "unknown"}:
                raise ValueError("Select a valid observed flow direction.")

    @staticmethod
    def _overlap(first, second):
        if not first or not second:
            return 0
        intersection = max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
            0, min(first[3], second[3]) - max(first[1], second[1]))
        area = (first[2]-first[0])*(first[3]-first[1]) + (second[2]-second[0])*(second[3]-second[1]) - intersection
        return intersection / area if area else 0

    def _stable_analysis_ids(self, sheet, components, connections):
        """Match positions, never detection-list indexes, before replaying human edits."""
        old = sheet.get("analysis", {})
        old_components = deepcopy(old.get("components", []))
        old_edges = deepcopy(old.get("connections", []))
        for correction in sheet["corrections"]:
            anchors = old_components if correction["collection"] == "components" else old_edges
            anchor = correction.get("baseline") or correction.get("record")
            if anchor and not find(anchors, correction["id"]):
                anchors.append(deepcopy(anchor))
        candidates = {item["id"]: [prior["id"] for prior in old_components
                      if self._overlap(item.get("bbox"), prior.get("bbox")) >= 0.6] for item in components}
        reserved = {item["id"] for item in old_components} | {
            c["id"] for c in sheet["corrections"] if c["collection"] == "components"}
        remap, used = {}, set()
        for item in components:
            choices = candidates[item["id"]]
            unambiguous = len(choices) == 1 and sum(choices[0] in match for match in candidates.values()) == 1
            target = choices[0] if unambiguous else item["id"]
            if not unambiguous and (target in reserved or target in used):
                target = ident("observation")
            remap[item["id"]] = target
            used.add(target)
        for item in components:
            item["id"] = remap[item["id"]]
        reserved_edges = {edge["id"] for edge in old_edges} | {
            c["id"] for c in sheet["corrections"] if c["collection"] == "connections"}
        used_edges = set()
        for edge in connections:
            edge["source"], edge["target"] = remap.get(edge["source"], edge["source"]), remap.get(edge["target"], edge["target"])
        edge_matches = {edge["id"]: [prior["id"] for prior in old_edges
            if {prior["source"], prior["target"]} == {edge["source"], edge["target"]} and
            self._same_route(prior.get("path"), edge.get("path"), sheet)] for edge in connections}
        edge_remap = {}
        for edge in connections:
            matches = edge_matches[edge["id"]]
            unambiguous = len(matches) == 1 and sum(matches[0] in match for match in edge_matches.values()) == 1
            target = matches[0] if unambiguous else edge["id"]
            if target in used_edges or (not unambiguous and target in reserved_edges):
                target = ident("edge")
            edge_remap[edge["id"]] = target
            edge["id"] = target
            used_edges.add(target)
        return remap, edge_remap

    @staticmethod
    def _same_route(first, second, sheet):
        if not first or not second:
            return True  # Endpoint matching still must be unique in both directions.
        def distance(point, start, end):
            dx, dy = end[0]-start[0], end[1]-start[1]
            scale = dx*dx+dy*dy
            t = max(0, min(1, ((point[0]-start[0])*dx+(point[1]-start[1])*dy)/scale)) if scale else 0
            return math.hypot(point[0]-start[0]-t*dx, point[1]-start[1]-t*dy)
        def directed(source, target):
            return max(min(distance(p,a,b) for a,b in zip(target,target[1:])) for p in source)
        return max(directed(first,second), directed(second,first)) <= max(3, min(sheet["width"],sheet["height"])*0.01)

    def replace_analysis(self, sheet_id, components, connections, provenance):
        components, connections = deepcopy(components), deepcopy(connections)
        def replace(state):
            sheet = self._sheet_for_edit(state, sheet_id)
            for item in components:
                self._validate_item(item, sheet, "components")
            for collection in (components, connections):
                if len({item["id"] for item in collection}) != len(collection):
                    raise ValueError("Analysis contains duplicate item IDs.")
            # Validate against the new equipment set before remapping identities.
            for edge in connections:
                self._validate_item(edge, {**sheet, "components": components}, "connections")
            component_remap, edge_remap = self._stable_analysis_ids(sheet, components, connections)
            normalized_provenance = deepcopy(provenance)
            for connector in normalized_provenance.get("connectors", []):
                connector["component_id"] = component_remap.get(connector["component_id"], connector["component_id"])
            for arrow in normalized_provenance.get("arrows", []):
                if arrow.get("connection_id"):
                    arrow["connection_id"] = edge_remap.get(arrow["connection_id"], arrow["connection_id"])
            sheet["analysis"] = {"components": deepcopy(components), "connections": deepcopy(connections),
                                  "provenance": normalized_provenance}
            sheet["components"], sheet["connections"] = deepcopy(components), deepcopy(connections)
            applied = []
            pending = []
            for correction in sorted(sheet["corrections"], key=lambda entry: entry["collection"] != "components"):
                collection = sheet[correction["collection"]]
                existing = find(collection, correction["id"])
                if correction.get("deleted"):
                    if existing:
                        collection.remove(existing)
                    applied.append(correction["id"])
                else:
                    candidate = deepcopy(correction["record"])
                    if existing:
                        collection[collection.index(existing)] = candidate
                    elif correction["collection"] == "components" and not correction.get("baseline"):
                        collection.append(candidate)
                    elif correction["collection"] == "components":
                        pending.append(correction["id"])
                        continue
                    elif correction.get("baseline"):
                        pending.append(correction["id"])
                        continue
                    else:
                        ids = {item["id"] for item in sheet["components"]}
                        if candidate["source"] in ids and candidate["target"] in ids:
                            collection.append(candidate)
                        else:
                            pending.append(correction["id"])
                            continue
                    applied.append(correction["id"])
            ids = {item["id"] for item in sheet["components"]}
            sheet["connections"] = [edge for edge in sheet["connections"] if edge["source"] in ids and edge["target"] in ids]
            sheet["source_metadata"]["analysis"] = normalized_provenance
            sheet["pending_corrections"] = pending
            self._touch(sheet)
            return {"sheet": sheet, "corrections_retained": len(applied), "pending": pending}
        return self.mutate(replace, "sheet.reanalyzed", details={"sheet_id": sheet_id})

    def save_link(self, link):
        def save(state):
            value = deepcopy(link)
            value.setdefault("id", ident("link"))
            if value.get("status") not in {"confirmed", "suggested", "rejected"}:
                raise ValueError("Select a connector link status.")
            endpoints = []
            for end in ("from", "to"):
                ref = value.get(end, {})
                sheet = find(state["sheets"], ref.get("sheet_id"))
                item = find(sheet["components"], ref.get("component_id")) if sheet else None
                item_type = str((item or {}).get("type", "")).lower().replace("-", " ").replace("_", " ")
                if item is None or item_type not in {"connector", "off page connector", "line continuation"}:
                    raise ValueError("Links must join two off-page connectors.")
                endpoints.append(sheet)
            if endpoints[0]["id"] == endpoints[1]["id"]:
                raise ValueError("Choose a connector on a different sheet.")
            if endpoints[0]["facility_id"] != endpoints[1]["facility_id"]:
                raise ValueError("Connector links must remain within a single facility.")
            value["facility_id"] = endpoints[0]["facility_id"]
            if value["status"] == "confirmed":
                for existing in state["links"]:
                    if existing["id"] != value["id"] and existing["status"] == "confirmed" and any(
                        existing[end] in (value["from"], value["to"]) for end in ("from", "to")):
                        raise Conflict("A connector already has a confirmed link. Reject that link before replacing it.")
            value.update(actor=state["settings"]["actor"], updated_at=now())
            previous = find(state["links"], value["id"])
            if previous:
                state["links"][state["links"].index(previous)] = value
            else:
                state["links"].append(value)
            return value
        return self.mutate(save, "connector.linked", details={"link_id": link.get("id"), "status": link.get("status")})

    def job(self, title, facility_id):
        value = {"id": ident("job"), "title": title, "facility_id": facility_id, "status": "queued",
                 "progress": 0, "messages": [], "created_at": now()}
        self.mutate(lambda state: state["jobs"].append(value))
        return value

    def update_job(self, job_id, **patch):
        def update(state):
            value = find(state["jobs"], job_id)
            if value is None:
                raise ValueError("Job was not found.")
            value.update(patch, updated_at=now())
            return value
        return self.mutate(update)
