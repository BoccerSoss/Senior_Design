"""Loopback HTTP workbench with local storage and read-only agent tools."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import csv
from email.parser import BytesParser
from email.policy import default as email_policy
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser
import zipfile

from PIL import Image

from ..io import ROOT
from . import VERSION
from .grid import equipment_positions
from .matrix import build_matrix, matrix_csv
from .ingest import Imports, MAX_UPLOAD
from .network import scope_isolation, search, trace_network
from .providers import ask_provider, extract_provider, provider_status
from .store import Conflict, Store, find, ident

STATIC = Path(__file__).with_name("static")
READ_TOOLS = [
    {"name": "search_drawings", "description": "Find drawings and equipment inside one research facility.",
     "inputSchema": {"type": "object", "properties": {"facility_id": {"type": "string"}, "query": {"type": "string"}},
                     "required": ["facility_id", "query"], "additionalProperties": False}},
    *[{"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": {"facility_id": {"type": "string"}, "sheet_id": {"type": "string"},
                                        "component_id": {"type": "string"}},
        "required": ["facility_id", "sheet_id", "component_id"], "additionalProperties": False}}
      for name, description in [
          ("find_component", "Look up a component and its source drawing evidence."),
          ("follow_flag", "Read the confirmed or proposed links at an off-page connector."),
          ("trace_lines", "Trace connected process piping across confirmed sheets."),
          ("isolation_boundary", "Return a candidate isolation boundary with explicit review gaps."),
          ("isolation_across_sheets", "Return the same candidate boundary across confirmed linked drawings."),
          ("view_region", "Return the component's image region and source metadata.")]],
]


class Application:
    def __init__(self, directory):
        self.store = Store(directory)
        self.imports = Imports(self.store)

    def close(self):
        self.imports.executor.shutdown(wait=False, cancel_futures=True)

    def state(self, facility_id=None):
        state = self.store.snapshot()
        if facility_id:
            state["sheets"] = [s for s in state["sheets"] if s["facility_id"] == facility_id]
            state["links"] = [link for link in state["links"] if link["facility_id"] == facility_id]
            state["jobs"] = [job for job in state["jobs"] if job["facility_id"] == facility_id]
        for sheet in state["sheets"]:
            sheet.pop("analysis", None)
            sheet.pop("corrections", None)
        state["audit"] = state["audit"][-500:]
        state["provider_status"] = self.providers()
        return state

    def providers(self):
        settings = self.store.snapshot()["settings"]
        if settings["provider"] == "local":
            return {"provider": "local", "ready": True, "external": False, "key_available": False,
                    "model": "Local graph queries", "choices": [
                        {"id": "local", "label": "Local graph queries", "key_env": None},
                        {"id": "openai", "label": "OpenAI", "key_env": "OPENAI_API_KEY"},
                        {"id": "anthropic", "label": "Anthropic", "key_env": "ANTHROPIC_API_KEY"},
                        {"id": "compatible", "label": "Approved gateway or local model", "key_env": "OPENAI_API_KEY"}]}
        return provider_status(settings)

    def suggest_links(self):
        state = self.store.snapshot()
        links = []
        endpoints = []
        for sheet in state["sheets"]:
            if not sheet.get("active", True):
                continue
            for component in sheet["components"]:
                component_type = str(component.get("type", "")).lower().replace("-", " ").replace("_", " ")
                if component_type in {"connector", "off page connector", "line continuation"}:
                    endpoints.append((sheet, component))
        existing = {frozenset((link["from"]["sheet_id"] + ":" + link["from"]["component_id"],
                               link["to"]["sheet_id"] + ":" + link["to"]["component_id"])) for link in state["links"]}
        for i, (sheet, component) in enumerate(endpoints):
            for other, target in endpoints[i + 1:]:
                if sheet["id"] == other["id"] or sheet["facility_id"] != other["facility_id"]:
                    continue
                requested = component.get("target_sheet", "").casefold()
                tag = component.get("connector_key") or component.get("tag")
                target_tag = target.get("connector_key") or target.get("tag")
                named = requested and requested in {other["drawing_number"].casefold(), other["title"].casefold()}
                equal = tag and target_tag and tag.casefold() == target_tag.casefold()
                key = frozenset((sheet["id"] + ":" + component["id"], other["id"] + ":" + target["id"]))
                if (named or equal) and key not in existing:
                    value = {"from": {"sheet_id": sheet["id"], "component_id": component["id"]},
                             "to": {"sheet_id": other["id"], "component_id": target["id"]},
                             "status": "suggested", "reason": "Matching connector text. Confirm against both drawings."}
                    links.append(self.store.save_link(value))
                    existing.add(key)
        return links

    def _component(self, facility_id, sheet_id, component_id):
        sheet = self.store.sheet(sheet_id)
        if sheet["facility_id"] != facility_id:
            raise ValueError("Component is outside the selected facility.")
        item = find(sheet["components"], component_id)
        if item is None:
            raise ValueError("Component was not found.")
        return sheet, item

    def tool(self, name, args):
        facility = args.get("facility_id")
        state = self.store.snapshot()
        if name == "search_drawings":
            return search(state, facility, args.get("query", ""))
        sheet_id, component_id = args.get("sheet_id"), args.get("component_id")
        sheet, item = self._component(facility, sheet_id, component_id)
        if name == "find_component":
            return {"sheet_id": sheet_id, "drawing_number": sheet["drawing_number"],
                    "revision": sheet["revision"], "component": item, "source": sheet["source_metadata"]}
        if name == "follow_flag":
            ref = {"sheet_id": sheet_id, "component_id": component_id}
            return {"links": [link for link in state["links"] if link["facility_id"] == facility
                              and ref in (link["from"], link["to"])]}
        if name == "trace_lines":
            return trace_network(state, facility, sheet_id, component_id)
        if name in {"isolation_boundary", "isolation_across_sheets"}:
            return scope_isolation(state, facility, sheet_id, component_id)
        if name == "view_region":
            return {"image_url": f"/api/region?sheet_id={sheet_id}&component_id={component_id}",
                    "sheet_id": sheet_id, "component_id": component_id, "bbox": item["bbox"],
                    "drawing_number": sheet["drawing_number"], "revision": sheet["revision"]}
        raise ValueError("Unknown agent tool.")

    def ask(self, body):
        import re

        facility, sheet_id, component_id = body.get("facility_id"), body.get("sheet_id"), body.get("component_id")
        question = body.get("question", "")
        if not isinstance(question, str) or not question.strip() or len(question) > 12000:
            raise ValueError("Enter a question under 12,000 characters.")
        question = question.strip()
        state = self.store.snapshot()
        settings = {**state["settings"], "provider": body.get("provider", state["settings"]["provider"])}
        if settings["provider"] == "local":
            if not isinstance(facility, str) or find(state["facilities"], facility) is None:
                raise ValueError("Choose a facility for the local question.")
            if component_id and not sheet_id:
                raise ValueError("Choose the drawing containing the selected component.")
            selected_sheet = self.store.sheet(sheet_id) if sheet_id else None
            if selected_sheet and selected_sheet["facility_id"] != facility:
                raise ValueError("The selected drawing is outside the selected facility.")
            scoped_sheets = [sheet for sheet in state["sheets"] if sheet["facility_id"] == facility
                             and sheet.get("active", True) and not sheet.get("superseded", False)
                             and (not sheet_id or sheet["id"] == sheet_id)]
            if selected_sheet and not scoped_sheets:
                raise ValueError("Choose an active drawing revision for the local question.")
            scoped = {**state, "sheets": scoped_sheets}
            available = search(scoped, facility, "")
            folded = question.casefold()
            words = set(re.findall(r"\w+", folded))
            isolate = bool(re.search(r"\b(isolat\w*|clearance|boundar\w*|close|closure|closures)\b", folded))
            tracing = isolate or bool(re.search(r"\b(trace\w*|connect\w*|flow\w*|upstream|downstream|piping)\b", folded))
            listing = bool(words & {"equipment", "components", "items"}) and not tracing

            # Match the actual observed tag as a complete phrase. No equipment
            # names, drawing IDs, or aliases are supplied by the query parser.
            tag_matches = []
            for item in available["components"]:
                tag = str(item.get("tag") or "").strip()
                if not tag:
                    continue
                # A one-letter label must retain its case, so the article "a"
                # in an ordinary sentence does not select equipment tagged A.
                haystack = question if len(tag) == 1 else folded
                needle = tag if len(tag) == 1 else tag.casefold()
                if re.search(r"(?<![\w-])" + re.escape(needle) + r"(?![\w-])", haystack):
                    tag_matches.append(item)

            # Type vocabulary comes from the scoped graph. Singular/plural
            # forms let "show pumps" match a recorded "centrifugal pump".
            query_words = words | {word[:-1] for word in words if len(word) > 3 and word.endswith("s")}
            type_terms = set()
            for item in available["components"]:
                type_words = set(re.findall(r"\w+", str(item.get("type") or "").casefold()))
                type_terms.update(type_words & query_words - {"unknown", "manual", "operated", "page", "line"})
            if tag_matches:
                candidates = tag_matches
            elif type_terms and not (tracing and component_id):
                candidates = [item for item in available["components"]
                              if set(re.findall(r"\w+", str(item.get("type") or "").casefold())) & type_terms]
            elif listing:
                candidates = available["components"]
            else:
                candidates = search(scoped, facility, question)["components"]
            candidate_sheets = {item["sheet_id"] for item in candidates}
            candidate_keys = {(item["sheet_id"], item["component_id"]) for item in candidates}
            matches = {"components": candidates,
                       "sheets": [sheet for sheet in available["sheets"] if sheet["id"] in candidate_sheets],
                       "connectors": [item for item in available["connectors"]
                                      if (item["sheet_id"], item["component_id"]) in candidate_keys]}
            trace = boundary = None
            target_sheet, target_component = sheet_id, component_id
            if target_component:
                self._component(facility, target_sheet, target_component)
            elif len(tag_matches) == 1:
                target_sheet, target_component = tag_matches[0]["sheet_id"], tag_matches[0]["component_id"]
            if tracing and target_component:
                trace = trace_network(state, facility, target_sheet, target_component)
                if isolate:
                    boundary = scope_isolation(state, facility, target_sheet, target_component)

            def label(item):
                return str(item.get("tag") or item.get("label") or item.get("component_id") or item.get("id"))

            def labels(items):
                values = [f"{label(item)} ({item.get('type', 'unknown')})" for item in items[:40]]
                if len(items) > 40:
                    values.append(f"{len(items) - 40} more observations")
                return ", ".join(values) or "none"

            if boundary:
                target = self._component(facility, target_sheet, target_component)[1]
                answer = (f"Candidate isolation boundary for {label(target)} has {len(boundary['closures'])} closure candidates: "
                          f"{labels(boundary['closures'])}. {len(boundary['open_ends'])} open ends or incomplete links "
                          f"and {len(boundary['warnings'])} review warnings remain. Check the highlighted piping and "
                          "recorded assumptions with the responsible engineer.")
            elif trace:
                target = self._component(facility, target_sheet, target_component)[1]
                answer = (f"Tracing {label(target)} reaches {len(trace['nodes'])} recorded components through process piping: "
                          f"{labels(trace['nodes'])}. Signal lines are excluded. "
                          f"{len(trace.get('warnings', []))} review warnings remain.")
            elif tracing:
                answer = (f"Found {len(candidates)} matching equipment observations: {labels(candidates)}. "
                          "Select one component or name one unique observed tag to trace its piping or review its candidate isolation boundary.")
            else:
                scope_name = selected_sheet["drawing_number"] or selected_sheet["title"] if selected_sheet else "the selected facility"
                answer = f"Found {len(candidates)} recorded equipment observations in {scope_name}: {labels(candidates)}."
                if not candidates:
                    answer += " Try an observed tag, a type such as pump or valve, or list the equipment on this sheet."
                elif any(item.get("review_status") != "confirmed" for item in candidates):
                    answer += " Some observations still require review against the drawing."

            source_ids = {item["sheet_id"] for item in trace["nodes"]} if trace else candidate_sheets
            if not source_ids:
                source_ids = {sheet["id"] for sheet in scoped_sheets}
            citations = [{key: sheet.get(key, "") for key in ("drawing_number", "title", "revision", "sheet_number")}
                         | {"sheet_id": sheet["id"]} for sheet in state["sheets"]
                         if sheet["facility_id"] == facility and sheet["id"] in source_ids]
            if citations:
                answer += " Sources: " + "; ".join(
                    f"{item['drawing_number'] or item['title']} (sheet {item['sheet_number'] or item['sheet_id']}, revision {item['revision'] or 'unrecorded'})"
                    for item in citations) + "."
            return {"answer": answer, "provider": "local", "model": "Local graph queries",
                    "citations": citations, "trace": trace, "isolation": boundary, "matches": matches}

        matches = search(state, facility, question)
        trace = boundary = None
        if sheet_id and component_id:
            self._component(facility, sheet_id, component_id)
            trace = trace_network(state, facility, sheet_id, component_id)
            if any(word in question.casefold() for word in ("isolat", "clearance", "boundary", "close")):
                boundary = scope_isolation(state, facility, sheet_id, component_id)
        citations = [{"sheet_id": sheet["id"], "drawing_number": sheet["drawing_number"],
                      "title": sheet["title"], "revision": sheet["revision"]}
                     for sheet in state["sheets"] if sheet["facility_id"] == facility and sheet.get("active", True)]
        context = {"facility_id": facility, "drawings": citations, "matches": matches,
                   "trace": trace, "candidate_boundary": boundary,
                   "instructions": "Use only this graph evidence, cite drawing numbers, and disclose gaps. "
                                   "A candidate boundary requires review and does not authorize plant operations."}
        answer = ask_provider(settings, question, context)
        answer["citations"] = citations
        self.store.mutate(lambda s: None, "ai.question", details={"provider": settings["provider"],
                                                               "facility_id": facility, "sheet_id": sheet_id})
        return answer

    def ai_extract(self, body):
        sheet = self.store.sheet(body["sheet_id"])
        settings = {**self.store.snapshot()["settings"],
                    "provider": body.get("provider", self.store.snapshot()["settings"]["provider"])}
        if settings["provider"] == "local":
            raise ValueError("Select a configured image-capable AI provider in Settings first.")
        job = self.store.job(f"AI read {sheet['title']}", sheet["facility_id"])
        def work():
            self.store.update_job(job["id"], status="running")
            try:
                image = (self.store.directory / "images" / f"{sheet['id']}.png").read_bytes()
                result = extract_provider(settings, image, "image/png", sheet["width"], sheet["height"])
                components = result["components"]
                for connector in result.get("connectors", []):
                    component = find(components, connector.get("component_id"))
                    if component:
                        for field in ("target_sheet", "target_tag", "observed_text"):
                            if connector.get(field):
                                component[field] = connector[field]
                        component["connector_key"] = connector.get("target_tag") or component.get("tag", "")
                for item in components:
                    item.setdefault("review_status", "unreviewed")
                    item.setdefault("normal_position", "unknown")
                    item.setdefault("isolation_credit", "unverified")
                connections = result["connections"]
                for index, edge in enumerate(connections):
                    edge.setdefault("id", f"edge-{index:04d}")
                    edge.setdefault("review_status", "unreviewed")
                response = self.store.directory / "ai" / f"{job['id']}.json"
                response.parent.mkdir(exist_ok=True)
                response.write_text(json.dumps(result, indent=2), encoding="utf-8")
                result = self.store.replace_analysis(sheet["id"], components, connections,
                    {"method": "image AI", "provider": result.get("provider", settings["provider"]), "model": result.get("model", settings["model"]),
                     "response": response.name, "uncertainties": result.get("uncertainties", []),
                     "arrows": result.get("arrows", []), "connectors": result.get("connectors", []),
                     "image": result.get("image", {}), "review_required": True})
                self.suggest_links()
                self.store.update_job(job["id"], status="completed", progress=100, sheet_ids=[sheet["id"]],
                                      messages=[f"Kept {result['corrections_retained']} corrections. Review new AI readings."])
            except (ValueError, OSError, RuntimeError) as exc:
                self.store.update_job(job["id"], status="failed", error=str(exc))
        self.imports.executor.submit(work)
        return job

    def action(self, body):
        action = body.get("action")
        if action == "create_facility":
            return {"result": self.store.add_facility(body.get("name"))}
        if action == "settings":
            return {"result": self.store.update_settings(body["settings"])}
        if action == "rename_user":
            return {"result": self.store.update_settings({"actor": body["actor"]})}
        if action == "update_sheet":
            return {"result": self.store.update_sheet(body["sheet_id"], body["patch"], body.get("version"))}
        if action in {"save_component", "save_connection"}:
            collection, key = ("components", "component") if action == "save_component" else ("connections", "connection")
            return {"result": self.store.edit_item(body["sheet_id"], collection, body[key],
                                                   expected_version=body.get("version"))}
        if action in {"delete_component", "delete_connection"}:
            collection, key = ("components", "component_id") if action == "delete_component" else ("connections", "connection_id")
            return {"result": self.store.edit_item(body["sheet_id"], collection, delete_id=body[key],
                                                   expected_version=body.get("version"))}
        if action == "save_link":
            return {"result": self.store.save_link(body["link"])}
        if action == "suggest_links":
            return {"result": self.suggest_links()}
        if action in {"trace", "isolate"}:
            function = trace_network if action == "trace" else scope_isolation
            args = (self.store.snapshot(), body["facility_id"], body["sheet_id"], body["component_id"])
            result = function(*args, include_signals=body.get("include_signals", False)) if action == "trace" else function(*args)
            return {"result": result}
        if action == "reanalyze":
            return {"job": self.imports.reanalyze(body["sheet_id"])}
        if action == "ask":
            return {"result": self.ask(body)}
        if action == "extract_ai":
            return {"job": self.ai_extract(body)}
        if action == "demo":
            kind = body.get("kind", "four-component")
            if kind == "four-component":
                drawing = ROOT / "data/input/four_component.png"
                if not drawing.is_file():
                    raise ValueError("The four-component input PNG is missing. Download the complete project folder.")
                facility = next((f for f in self.store.snapshot()["facilities"] if f["name"] == "Four-component example"), None)
                facility = facility or self.store.add_facility("Four-component example")
                index = b"file,title,drawing,sheet\nfour_component.png,Synthetic P&ID test,Four-component example,1\n"
                # The demo runs the same image extractor as an ordinary upload.
                # No reference components, tags or edges are supplied here.
                job = self.imports.submit([(drawing.name, drawing.read_bytes()), ("index.csv", index)], facility["id"])
                return {"job": job, "facility_id": facility["id"]}
            if kind == "beginner":
                files = []
                candidates = [ROOT.parent.parent / "outputs/diagram-pages-png/01_Beginner",
                              ROOT / "data/input/beginner"]
                folder = next((p for p in candidates if p.is_dir()), None)
                if folder is None:
                    raise ValueError("Import your beginner PNG folder using Import drawings.")
                for file in sorted(folder.glob("*.png")):
                    files.append((file.name, file.read_bytes()))
                facility = next((f for f in self.store.snapshot()["facilities"] if f["name"] == "ISLOCA beginner collection"), None)
                facility = facility or self.store.add_facility("ISLOCA beginner collection")
                return {"job": self.imports.submit(files, facility["id"]), "facility_id": facility["id"]}
            from .demo import create_demo
            return {"result": create_demo(self.store, kind=kind)}
        raise ValueError("Unknown application action.")

    def matrix(self, facility_id=None, sheet_id=None):
        if sheet_id and not facility_id:
            facility_id = self.store.sheet(sheet_id)["facility_id"]
        if not facility_id:
            raise ValueError("Choose a drawing or facility for the connectivity matrix.")
        return build_matrix(self.store.snapshot(), facility_id, sheet_id)

    def matrix_query(self, query):
        if query.get("method", ["v2"])[0] != "v2":
            raise ValueError("Unknown matrix method.")
        return self.matrix(query.get("facility_id", [None])[0], query.get("sheet_id", [None])[0])

    def grid_query(self, query):
        if query.get("method", ["v2"])[0] != "v2":
            raise ValueError("Unknown grid method.")
        return equipment_positions(self.store.sheet(query.get("sheet_id", [None])[0]))

    def export(self, query):
        state = self.state(query.get("facility_id", [None])[0])
        sheet_id = query.get("sheet_id", [None])[0]
        kind = query.get("format", ["json"])[0]
        if kind == "grid":
            return json.dumps(self.grid_query(query), indent=2).encode(), "application/json", "equipment-grid.json"
        if kind in {"matrix", "matrix-json", "connections", "connections-json"}:
            result = self.matrix_query(query)
            if kind == "matrix":
                return matrix_csv(result).encode("utf-8-sig"), "text/csv", "connectivity-matrix.csv"
            if kind == "matrix-json":
                return json.dumps(result, indent=2).encode(), "application/json", "connectivity-matrix.json"
            if kind == "connections-json":
                value = {"scope": result["scope"], "connections": result["direct_edges"], "diagnostics": result["diagnostics"]}
                return json.dumps(value, indent=2).encode(), "application/json", "direct-connections.json"
            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerow(["source", "target", "source_sheet_id", "source_component_id", "target_sheet_id", "target_component_id", "review_status"])
            for edge in result["direct_edges"]:
                writer.writerow([edge["source_label"], edge["target_label"], edge["source"]["sheet_id"], edge["source"]["component_id"],
                                 edge["target"]["sheet_id"], edge["target"]["component_id"], edge["review_status"]])
            return stream.getvalue().encode("utf-8-sig"), "text/csv", "direct-connections.csv"
        if kind == "csv":
            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerow(["facility", "drawing", "sheet", "revision", "component_id", "tag", "type", "review", "normal_position"])
            for sheet in state["sheets"]:
                if sheet_id and sheet["id"] != sheet_id:
                    continue
                for item in sheet["components"]:
                    writer.writerow([sheet["facility_id"], sheet["drawing_number"], sheet["sheet_number"], sheet["revision"],
                                     item["id"], item.get("tag"), item["type"], item["review_status"], item["normal_position"]])
            return stream.getvalue().encode("utf-8-sig"), "text/csv", "equipment.csv"
        if kind == "zip":
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("facility.json", json.dumps(state, indent=2))
                for sheet in state["sheets"]:
                    image = self.store.directory / "images" / f"{sheet['id']}.png"
                    if image.is_file():
                        archive.write(image, f"images/{sheet['id']}.png")
            return buffer.getvalue(), "application/zip", "drawing-workspace.zip"
        return json.dumps(state, indent=2).encode(), "application/json", "drawing-workspace.json"


class Handler(BaseHTTPRequestHandler):
    server_version = "AIPV2/" + VERSION
    app_version = VERSION
    read_tools = READ_TOOLS
    MCP_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")

    @property
    def app(self):
        return self.server.app

    def log_message(self, format, *args):
        return

    def extra_get(self, path, query):
        return False

    def extra_post(self, path, body):
        return False

    def _host_ok(self):
        name = self.headers.get("Host", "").split(":")[0]
        return name in {"127.0.0.1", "localhost"}

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        return not origin or origin == "http://" + self.headers.get("Host", "")

    def _authorized(self):
        supplied = self.headers.get("X-API-KEY") or self.headers.get("Authorization", "").removeprefix("Bearer ")
        try:
            supplied = supplied.encode("ascii")
        except UnicodeEncodeError:
            return False
        expected = self.app.store.snapshot(private=True)["api_key"].encode("ascii")
        return hmac.compare_digest(supplied, expected)

    def send(self, data, status=200, content_type="application/json", download=None, headers=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        if isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            if not self._host_ok() or not self._origin_ok():
                return self.send({"error": "Cross-origin request denied."}, 403)
            parsed = urlsplit(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if self.extra_get(path, query):
                return
            if path == "/mcp":
                if not self._authorized():
                    return self.send({"error": "API key required."}, 401)
                if self.headers.get("MCP-Protocol-Version", "2025-03-26") not in self.MCP_VERSIONS:
                    return self.send({"error": "Unsupported MCP protocol version."}, 400)
                return self.send({"error": "This stateless endpoint does not offer an SSE stream."}, 405,
                                 headers={"Allow": "POST"})
            if path == "/api/state":
                return self.send(self.app.state(query.get("facility_id", [None])[0]))
            if path == "/api/providers":
                return self.send(self.app.providers())
            if path == "/api/matrix":
                return self.send(self.app.matrix_query(query))
            if path == "/api/grid":
                return self.send(self.app.grid_query(query))
            if path.startswith("/api/jobs/"):
                job = find(self.app.store.snapshot()["jobs"], path.rsplit("/", 1)[-1])
                return self.send(job or {"error": "Job was not found."}, 200 if job else 404)
            if path.startswith("/api/images/"):
                sheet = self.app.store.sheet(path.rsplit("/", 1)[-1])
                image = self.app.store.directory / "images" / f"{sheet['id']}.png"
                return self.send(image.read_bytes(), content_type="image/png")
            if path == "/api/region":
                sheet = self.app.store.sheet(query["sheet_id"][0])
                item = find(sheet["components"], query["component_id"][0])
                if not item or not item.get("bbox"):
                    raise ValueError("This component has no image region.")
                box = item["bbox"]
                pad = max(80, box[2] - box[0], box[3] - box[1])
                with Image.open(self.app.store.directory / "images" / f"{sheet['id']}.png") as image:
                    crop = image.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                                       min(image.width, box[2] + pad), min(image.height, box[3] + pad)))
                    buffer = io.BytesIO()
                    crop.save(buffer, "PNG")
                    return self.send(buffer.getvalue(), content_type="image/png")
            if path == "/api/export":
                data, mime, name = self.app.export(query)
                return self.send(data, content_type=mime, download=name)
            if path == "/api/integration":
                # Returned only to a same-origin loopback application or an authenticated API caller.
                if not self._origin_ok():
                    return self.send({"error": "Cross-origin access denied."}, 403)
                endpoint = "http://" + self.headers["Host"]
                return self.send({"url": endpoint + "/mcp", "api_key": self.app.store.snapshot(private=True)["api_key"],
                                  "tools": self.read_tools, "rest": endpoint + "/api/v1/docs",
                                  "scope": "Read-only facility graph and candidate isolation tools."})
            if path.startswith("/api/v1/"):
                if not self._authorized():
                    return self.send({"error": "Set X-API-KEY or Authorization Bearer."}, 401)
                if path == "/api/v1/docs":
                    return self.send({"tools": self.read_tools, "invoke": "POST /api/v1/tools/{tool_name} with tool arguments",
                                      "facilities": "/api/v1/facilities", "drawings": "/api/v1/drawings?facility_id=...",
                                      "graph": "/api/v1/graph?facility_id=...", "mcp": "/mcp"})
                if path == "/api/v1/facilities":
                    return self.send(self.app.state()["facilities"])
                if path in {"/api/v1/drawings", "/api/v1/graph"}:
                    return self.send(self.app.state(query.get("facility_id", [None])[0]))
                raise ValueError("Unknown API resource.")
            if path == "/reference":
                return self.send((ROOT / "docs/symbol_reference.html").read_bytes(), content_type="text/html; charset=utf-8")
            filename = "index.html" if path == "/" else path.removeprefix("/")
            if filename not in {"index.html", "app.js", "style.css"}:
                return self.send({"error": "Page was not found."}, 404)
            content = (STATIC / filename).read_bytes()
            return self.send(content, content_type=mimetypes.guess_type(filename)[0] + "; charset=utf-8")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.send({"error": str(exc)}, 400)

    def do_POST(self):
        try:
            if not self._host_ok() or not self._origin_ok():
                return self.send({"error": "Cross-origin request denied."}, 403)
            parsed = urlsplit(self.path)
            if parsed.path == "/mcp" or parsed.path.startswith("/api/v1/"):
                if not self._authorized():
                    return self.send({"error": "API key required."}, 401)
            if parsed.path == "/mcp":
                if self.headers.get("MCP-Protocol-Version", "2025-03-26") not in self.MCP_VERSIONS:
                    return self.send({"error": "Unsupported MCP protocol version."}, 400)
                if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                    return self.send({"error": "MCP requests require application/json."}, 415)
                accepted = {part.split(";")[0].strip().lower() for part in self.headers.get("Accept", "").split(",")}
                if not {"application/json", "text/event-stream"}.issubset(accepted) and "*/*" not in accepted:
                    return self.send({"error": "Accept must include application/json and text/event-stream."}, 406)
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= MAX_UPLOAD:
                return self.send({"error": "Request is empty or exceeds 150 MB."}, 413)
            if parsed.path == "/mcp" and length > 1024 * 1024:
                return self.send({"error": "MCP request exceeds 1 MiB."}, 413)
            content = self.rfile.read(length)
            if parsed.path == "/api/import":
                mime = self.headers.get("Content-Type", "")
                if not mime.startswith("multipart/form-data"):
                    raise ValueError("Use multipart drawing uploads.")
                message = BytesParser(policy=email_policy).parsebytes(
                    ("Content-Type: " + mime + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + content)
                files, facility = [], None
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if part.get_filename():
                        files.append((part.get_filename(), part.get_payload(decode=True)))
                    elif name == "facility_id":
                        facility = part.get_content().strip()
                return self.send({"job": self.app.imports.submit(files, facility)}, 202)
            def invalid_constant(value):
                raise ValueError("Non-finite JSON number.")
            try:
                body = json.loads(content.decode("utf-8"), parse_constant=invalid_constant)
            except (ValueError, UnicodeError, RecursionError):
                if parsed.path == "/mcp":
                    return self.send({"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32700, "message": "Invalid JSON request."}}, 400)
                raise ValueError("Request contains invalid JSON.") from None
            if parsed.path == "/mcp":
                return self._mcp(body)
            if not isinstance(body, dict):
                raise ValueError("Request must be a JSON object.")
            if self.extra_post(parsed.path, body):
                return
            if parsed.path == "/api/action":
                result = self.app.action(body)
                result["state"] = self.app.state()
                return self.send(result)
            if parsed.path.startswith("/api/v1/tools/"):
                if not self._authorized():
                    return self.send({"error": "API key required."}, 401)
                return self.send(self.app.tool(parsed.path.rsplit("/", 1)[-1], body))
            return self.send({"error": "Unknown endpoint."}, 404)
        except Conflict as exc:
            self.send({"error": str(exc)}, 409)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.send({"error": str(exc)}, 400)

    def do_DELETE(self):
        if not self._host_ok() or not self._origin_ok():
            return self.send({"error": "Cross-origin request denied."}, 403)
        if urlsplit(self.path).path != "/mcp":
            return self.send({"error": "Unknown endpoint."}, 404)
        if not self._authorized():
            return self.send({"error": "API key required."}, 401)
        if self.headers.get("MCP-Protocol-Version", "2025-03-26") not in self.MCP_VERSIONS:
            return self.send({"error": "Unsupported MCP protocol version."}, 400)
        return self.send({"error": "This stateless endpoint does not maintain sessions."}, 405,
                         headers={"Allow": "POST"})

    def _mcp(self, body, collect=False):
        """Serve read-only JSON-RPC without creating per-client HTTP sessions."""
        request_id = None
        def reply(data, status=200):
            return (status, data) if collect else self.send(data, status)
        def fail(code, message, status=200):
            return reply({"jsonrpc": "2.0", "id": request_id,
                          "error": {"code": code, "message": message}}, status)
        if isinstance(body, list):
            if (collect or self.headers.get("MCP-Protocol-Version", "2025-03-26") != "2025-03-26"
                    or not 1 <= len(body) <= 100):
                return fail(-32600, "Invalid or unsupported JSON-RPC batch.", 400)
            replies = []
            for message in body:
                status, data = self._mcp(message, collect=True)
                if status != 202:
                    replies.append(data)
            return reply(replies) if replies else reply(b"", 202)
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            return fail(-32600, "Invalid JSON-RPC request.", 400)
        if "id" in body:
            value = body["id"]
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                return fail(-32600, "Request ID must be a string or integer.", 400)
            request_id = value
        method = body.get("method")
        if method is None:
            # No server-initiated requests exist in this stateless implementation;
            # accepting a client response only acknowledges receipt, never runs a tool.
            valid_error = isinstance(body.get("error"), dict) and isinstance(body["error"].get("code"), int) and isinstance(body["error"].get("message"), str)
            if "id" in body and (("result" in body and "error" not in body) or ("result" not in body and valid_error)):
                return reply(b"", 202)
            return fail(-32600, "Invalid JSON-RPC message.", 400)
        if not isinstance(method, str) or not method:
            return fail(-32600, "JSON-RPC method must be text.", 400)
        params = body.get("params", {})
        if not isinstance(params, dict):
            return fail(-32602, "MCP parameters must be a JSON object.", 400 if "id" not in body else 200)
        if "id" not in body:
            # A notification must never receive a JSON-RPC response or execute a
            # request method. Unknown notifications are safely ignored.
            return reply(b"", 202)
        if method.startswith("notifications/"):
            return fail(-32600, "Notifications must omit the request ID.")
        if method == "initialize":
            client = params.get("clientInfo")
            version = params.get("protocolVersion")
            if (not isinstance(version, str) or not version or not isinstance(params.get("capabilities"), dict)
                    or not isinstance(client, dict) or not isinstance(client.get("name"), str)
                    or not isinstance(client.get("version"), str)):
                return fail(-32602, "Initialize requires protocolVersion, capabilities, and clientInfo name/version.")
            result = {"protocolVersion": version if version in self.MCP_VERSIONS else self.MCP_VERSIONS[0],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "aip-local-workbench", "version": self.app_version}}
        elif method == "tools/list":
            if params.get("cursor") is not None:
                return fail(-32602, "This tool list does not use pagination cursors.")
            result = {"tools": [{**tool, "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                                         "idempotentHint": True, "openWorldHint": False}}
                                for tool in self.read_tools]}
        elif method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return fail(-32602, "Tool call requires a name and an arguments object.")
            tool = next((value for value in self.read_tools if value["name"] == name), None)
            if tool is None:
                return fail(-32602, "Unknown read-only tool.")
            schema = tool["inputSchema"]
            if (set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments)
                    or any(not isinstance(value, str) or len(value) > 12000 for value in arguments.values())):
                return fail(-32602, "Tool arguments do not match the declared input schema.")
            try:
                data = self.app.tool(name, arguments)
                result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, allow_nan=False)}],
                          "structuredContent": data, "isError": False}
            except (ValueError, KeyError, TypeError) as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        elif method == "ping":
            result = {}
        else:
            return fail(-32601, "Unsupported MCP method.")
        return reply({"jsonrpc": "2.0", "id": request_id, "result": result})


def serve(directory, port=8765, open_browser=True, *, handler=Handler, application=Application, version=VERSION):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.app = application(directory)
    address = f"http://127.0.0.1:{server.server_address[1]}"
    (server.app.store.directory / "server.json").write_text(
        json.dumps({"url": address, "pid": os.getpid(), "version": version}), encoding="utf-8")
    print(f"AIP {version} running at {address}", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.app.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=ROOT / "runs/v2")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    serve(args.data, args.port, not args.no_browser)


if __name__ == "__main__":
    main()
