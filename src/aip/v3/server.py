"""Serve the V3 drawing workbench and its isolated AI predictions on loopback."""
import argparse
from functools import lru_cache, partial
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import threading
from time import perf_counter

from ..drawing_reference import reference_image_keys
from ..io import ROOT, digest
from ..review import review_queue
from ..v2.grid import equipment_positions
from ..v2.matrix import build_matrix
from ..v2.providers import provider_status
from ..v2.server import Application as BaseApplication, Handler as BaseHandler, READ_TOOLS, serve
from ..v2.store import Conflict, now
from . import VERSION
from .ask import ask_drawing
from .beginner import preload_beginner
from .plan import PlanClient
from .vision import MODEL_ID, extract_connections, refresh_trace_directions

REVIEW_FIELDS = {"component": ("tag", "type"), "connection": ("direction", "kind")}
DIRECTIONS = {"source_to_target", "target_to_source", "bidirectional", "unknown"}
LINE_KINDS = {"pipe", "direct_undirected", "signal", "unknown"}
DIRECTION_REVISION = 3


@lru_cache(maxsize=512)
def _png_digest(path, modified_ns, size):
    return digest(path)


def profile(profile_id):
    if profile_id == "local-ml":
        return {"provider": profile_id, "model": MODEL_ID}
    if profile_id == "local-graph":
        return {"provider": profile_id, "model": "Local graph lookup"}
    if profile_id == "openai":
        return {"provider": "openai", "model": os.getenv("AIP_OPENAI_MODEL", ""),
                "base_url": "https://api.openai.com/v1"}
    if profile_id == "anthropic":
        return {"provider": "anthropic", "model": os.getenv("AIP_ANTHROPIC_MODEL", ""),
                "base_url": "https://api.anthropic.com/v1"}
    if profile_id == "compatible":
        return {"provider": "compatible", "model": os.getenv("AIP_GATEWAY_MODEL", ""),
                "base_url": os.getenv("AIP_GATEWAY_BASE_URL", ""), "credential_env": "AIP_GATEWAY_API_KEY"}
    raise ValueError("Choose a listed AI provider.")


def available_profiles(overrides=None):
    values = []
    for key, label in (("local-ml", "Local connection model"), ("local-graph", "Local graph lookup"),
                       ("openai", "OpenAI (GPT)"), ("anthropic", "Claude (Anthropic)"),
                       ("compatible", "Organization gateway")):
        settings = {**profile(key), **(overrides or {}).get(key, {})}
        if key.startswith("local-"):
            status = {"ready": True, "external": False, "error": None}
        elif not settings["model"] or not settings["base_url"]:
            env_name = "GATEWAY" if key == "compatible" else key.upper()
            hint = f"Set AIP_{env_name}_MODEL to the model ID on your account."
            if key == "compatible" and not settings["base_url"]:
                hint += " Set AIP_GATEWAY_BASE_URL to your server's API base."
            status = {"ready": False, "external": True, "error": hint}
        else:
            status = provider_status(settings)
        values.append({"id": key, "label": label, "model": settings["model"],
                       "trace": key != "local-graph", "ask": key != "local-ml",
                       **{field: status[field] for field in ("ready", "external", "error")}})
    return values


class Application(BaseApplication):
    def __init__(self, directory):
        new_workspace = not (Path(directory) / "workbench.sqlite3").exists()
        super().__init__(directory)
        if new_workspace:
            self.store.update_settings({"use_geometry_reference": False})
        self._in_flight = set()
        self._lock = threading.Lock()
        self._profiles = {}
        self._reference_keys = reference_image_keys()
        self.plan = PlanClient()
        with self.store.db:
            self.store.db.execute("CREATE TABLE IF NOT EXISTS v3_traces "
                                  "(sheet_id TEXT PRIMARY KEY, result TEXT NOT NULL)")
            self.store.db.execute("CREATE TABLE IF NOT EXISTS v3_reviewed_traces "
                                  "(sheet_id TEXT NOT NULL, trace_id TEXT NOT NULL, result TEXT NOT NULL, "
                                  "PRIMARY KEY (sheet_id, trace_id))")
        self.beginner_collection = preload_beginner(self.store)

    def action(self, body):
        if body.get("action") == "read_symbol_reference":
            sheet = self.store.sheet(body.get("sheet_id"))
            if not self.has_symbol_reference(sheet):
                raise ValueError("There is no matching symbol reference for this drawing image.")
            return {"job": self.imports.reanalyze(sheet["id"], require_reference=True)}
        if body.get("action") == "demo" and body.get("kind") == "beginner":
            return {"result": self.beginner_collection}
        return super().action(body)

    def has_symbol_reference(self, sheet):
        path = self.store.directory / "images" / (sheet["id"] + ".png")
        try:
            stat = path.stat()
            fingerprint = _png_digest(path, stat.st_mtime_ns, stat.st_size)
        except OSError:
            return False
        return (fingerprint, sheet.get("width"), sheet.get("height")) in self._reference_keys

    def state(self, facility_id=None):
        state = super().state(facility_id)
        state["capabilities"] = {"ai_connections": True, "local_trace_version": VERSION}
        sheets = {sheet["id"]: sheet for sheet in state["sheets"]}
        for sheet in sheets.values():
            sheet["has_symbol_reference"] = self.has_symbol_reference(sheet)
        with self.store.lock:
            # The library can search the saved names without loading every detailed trace into the browser.
            for sheet_id, count, names in self.store.db.execute("""
                SELECT sheet_id, json_array_length(result, '$.components'),
                       (SELECT json_group_array(json_extract(value, '$.tag')) FROM json_each(result, '$.components')
                        WHERE json_extract(value, '$.tag') <> '')
                FROM v3_traces WHERE sheet_id IN (SELECT value FROM json_each(?))
            """, (json.dumps(list(sheets)),)):
                sheets[sheet_id]["v3_summary"] = {"components": count, "names": json.loads(names)}
        return state

    def list_profiles(self):
        with self._lock:
            profiles = available_profiles(self._profiles)
        profiles.insert(2, {"id": "chatgpt-plan", "label": "ChatGPT subscription",
                            "trace": True, "ask": True, "external": True, **self.plan.status()})
        return profiles

    def close(self):
        self.plan.close()
        super().close()

    def connect_profile(self, body):
        key, model, secret = body.get("provider"), body.get("model"), body.get("api_key")
        if key not in ("openai", "anthropic"):
            raise ValueError("Select OpenAI or Claude for a session connection.")
        if not isinstance(model, str) or not 1 <= len(model.strip()) <= 256 or not model.isprintable():
            raise ValueError("Enter the API model ID for this account.")
        if not isinstance(secret, str) or not 1 <= len(secret) <= 8192 or not secret.isascii() or not all(
                33 <= ord(c) <= 126 for c in secret):
            raise ValueError("Enter the provider API key without whitespace.")
        with self._lock:
            self._profiles[key] = {"model": model.strip(), "_session_key": secret}
        return {"configured": True}

    def require_profile(self, key, capability, model=None):
        if key == "chatgpt-plan":
            status = self.plan.status()
            if not status["ready"]:
                raise ValueError(status["error"] or "Continue with ChatGPT to use your subscription.")
            model = status["model"] if model is None else model
            if not isinstance(model, str) or model not in [item["slug"] for item in status["models"]]:
                raise ValueError("Select a model available to this ChatGPT account.")
            return {"provider": key, "model": model, "_plan_generate": partial(self.plan.generate, model=model)}
        with self._lock:
            for info in available_profiles(self._profiles):
                if key == info["id"]:
                    if not info[capability]:
                        raise ValueError(f"{info['label']} does not support this action.")
                    if not info["ready"]:
                        raise ValueError(info["error"])
                    return {**profile(key), **self._profiles.get(key, {})}
        raise ValueError("Choose a listed AI provider.")

    def latest(self, sheet_id):
        self.store.sheet(sheet_id)
        return self.trace_result(self._stored_trace(sheet_id))

    @staticmethod
    def trace_result(trace):
        if trace is None:
            return None
        return {**trace, "review_revision": trace.get("review_revision", 0), "review": review_queue(trace)}

    def _stored_trace(self, sheet_id):
        with self.store.lock:
            row = self.store.db.execute("SELECT result FROM v3_traces WHERE sheet_id=?", (sheet_id,)).fetchone()
            if not row:
                return None
            trace = json.loads(row[0])
            if trace.get("direction_revision", 0) >= DIRECTION_REVISION:
                return trace
            try:
                image = (self.store.directory / "images" / f"{sheet_id}.png").read_bytes()
                refreshed = refresh_trace_directions(trace, image)
            except (OSError, ValueError):
                return trace
            reviewed = {edge["id"]: edge for edge in trace["connections"]
                        if edge.get("review_status") in ("confirmed", "rejected")
                        or edge.get("ai_observation") or (edge.get("direction_evidence") or {}).get("basis") == "reviewed"}
            refreshed["connections"] = [reviewed.get(edge["id"], edge) for edge in refreshed["connections"]]
            refreshed["direction_revision"] = DIRECTION_REVISION
            signature = refreshed.get("local_input")
            if (signature and signature.get("version") in {"3.3.0", "3.4.0"}
                    and signature.get("sha256") == sha256(image).hexdigest()):
                signature["version"] = VERSION
            with self.store.db:
                self.store.db.execute("UPDATE v3_traces SET result=? WHERE sheet_id=?",
                                      (json.dumps(refreshed, allow_nan=False), sheet_id))
            return refreshed

    def review_trace(self, body):
        sheet_id = self.store.sheet(body.get("sheet_id"))["id"]
        kind = body.get("kind")
        fields = REVIEW_FIELDS.get(kind) if isinstance(kind, str) else None
        changes = body.get("changes")
        if not fields or not isinstance(changes, dict) or set(changes) - set(fields):
            raise ValueError("Choose an equipment or connection field to review.")
        revision = body.get("revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("Send the revision of the displayed V3 trace.")
        status = body.get("status")
        if status not in ("confirmed", "unreviewed"):
            raise ValueError("Choose a valid review status.")
        normalized = {}
        for name, value in changes.items():
            if not isinstance(value, str) or len(value) > 250 or (value and not value.isprintable()):
                raise ValueError("Enter short, printable text for the correction.")
            normalized[name] = value.strip()
        if "type" in normalized and not normalized["type"]:
            normalized["type"] = "unknown"
        if "direction" in normalized and normalized["direction"] not in DIRECTIONS:
            raise ValueError("Choose a valid observed direction.")
        if "kind" in normalized and normalized["kind"] not in LINE_KINDS:
            raise ValueError("Choose a supported line type.")

        with self._lock, self.store.lock, self.store.db:
            if sheet_id in self._in_flight:
                raise Conflict("Wait for the new trace to finish before reviewing this drawing.")
            trace = self._stored_trace(sheet_id)
            if (not trace or trace.get("created_at") != body.get("trace_id")
                    or trace.get("review_revision", 0) != revision):
                raise Conflict("The displayed V3 trace changed. Refresh it before saving.")
            collection = "components" if kind == "component" else "connections"
            item = next((item for item in trace[collection] if item["id"] == body.get("id")), None)
            if item is None:
                raise ValueError("The item was not found in the displayed trace.")
            changed = any(item.get(key) != value for key, value in normalized.items())
            if not changed and item.get("review_status", "unreviewed") == status:
                return self.trace_result(trace)
            evidence_fields = (*fields, "review_status", "direction_evidence")
            item.setdefault("ai_observation", {key: item[key] for key in evidence_fields if key in item})
            if "direction" in normalized and normalized["direction"] != item.get("direction", "unknown"):
                item["direction_evidence"] = {"basis": "reviewed", "arrow_ids": []}
            item.update(normalized, review_status=status)
            trace["review_revision"] = revision + 1
            self.store.db.execute("UPDATE v3_traces SET result=? WHERE sheet_id=?",
                                  (json.dumps(trace, allow_nan=False), sheet_id))
        return self.trace_result(trace)

    def trace(self, body):
        sheet = self.store.sheet(body.get("sheet_id"))
        sheet_id = sheet["id"]
        settings = self.require_profile(body.get("provider"), "trace", body.get("model"))
        refresh = body.get("refresh", False)
        replace_reviewed = body.get("replace_reviewed", False)
        if not isinstance(refresh, bool) or not isinstance(replace_reviewed, bool):
            raise ValueError("The refresh and replace-reviewed flags must be true or false.")
        with self._lock:
            if sheet_id in self._in_flight:
                raise Conflict("A connection trace is already running for this drawing.")
            self._in_flight.add(sheet_id)
        try:
            image = (self.store.directory / "images" / f"{sheet_id}.png").read_bytes()
            local = settings["provider"] == "local-ml"
            previous = self._stored_trace(sheet_id)
            if local:
                signature = {"sha256": sha256(image).hexdigest(), "model": settings["model"], "version": VERSION}
                if not refresh and previous and previous.get("local_input") == signature:
                    return {**self.trace_result(previous), "reused": True}
            has_reviews = previous and previous.get("review_revision", 0)
            if has_reviews and not replace_reviewed:
                raise Conflict("This trace has saved reviews. Confirm that you want to replace it before tracing again.")
            start = perf_counter()
            trace = extract_connections(settings, image, "image/png", sheet["width"], sheet["height"])
            trace.update(sheet_id=sheet_id, created_at=now(), duration_seconds=round(perf_counter() - start, 3),
                         direction_revision=DIRECTION_REVISION)
            if local:
                trace["local_input"] = signature
            encoded = json.dumps(trace, allow_nan=False)
            with self.store.lock, self.store.db:
                if has_reviews:
                    self.store.db.execute("INSERT INTO v3_reviewed_traces VALUES (?,?,?)",
                                          (sheet_id, previous["created_at"], json.dumps(previous, allow_nan=False)))
                self.store.db.execute("INSERT OR REPLACE INTO v3_traces VALUES (?,?)", (sheet_id, encoded))
            return self.trace_result(trace)
        finally:
            with self._lock:
                self._in_flight.remove(sheet_id)

    def selected_graph(self, sheet_id, method, facility_id=None):
        sheet = self.store.sheet(sheet_id)
        if facility_id is not None and facility_id != sheet["facility_id"]:
            raise ValueError("Drawing is outside the selected collection.")
        if method == "v2":
            sheet = self.checked_drawing(sheet)
            return sheet, self.matrix(sheet["facility_id"], sheet_id)
        if method != "v3":
            raise ValueError("Select the V2 or V3 extraction.")
        trace = self.latest(sheet_id)
        if trace is None:
            raise ValueError("Trace this drawing with V3 first.")
        sheet.update(components=trace["components"], connections=trace["connections"])
        state = {"facilities": [{"id": sheet["facility_id"]}], "sheets": [sheet]}
        matrix = build_matrix(state, sheet["facility_id"], sheet_id, include_unreviewed_warnings=False)
        matrix["scope"].update(method="v3", model=trace["model"], provider=trace["provider"])
        matrix["arrows"] = trace.get("arrows", [])
        matrix["review"] = trace["review"]
        matrix["stats"]["review_items"] = len(trace["review"])
        return sheet, matrix

    def matrix_query(self, query):
        method = query.get("method", ["v2"])[0]
        if method == "v2":
            return super().matrix_query(query)
        return self.selected_graph(query.get("sheet_id", [None])[0], method, query.get("facility_id", [None])[0])[1]

    def grid_query(self, query):
        method = query.get("method", ["v2"])[0]
        if method == "v2":
            return super().grid_query(query)
        sheet, _ = self.selected_graph(query.get("sheet_id", [None])[0], method)
        return equipment_positions(sheet, query.get("layout", ["source"])[0])

    def ask_selected(self, body):
        settings = self.require_profile(body.get("provider"), "ask", body.get("model"))
        sheet, matrix = self.selected_graph(body.get("sheet_id"), body.get("method", "v2"))
        return ask_drawing(settings, body.get("question"), sheet, matrix, grid_layout=body.get("grid_layout", "source"))

    def tool(self, name, args):
        if name == "list_drawings":
            state = self.store.snapshot()
            with self.store.lock:
                traced = {row[0] for row in self.store.db.execute("SELECT sheet_id FROM v3_traces")}
            return {"drawings": [{key: sheet.get(key) for key in (
                    "id", "facility_id", "title", "drawing_number", "sheet_number", "revision", "active")}
                    | {"methods": ["v2", "v3"] if sheet["id"] in traced else ["v2"]} for sheet in state["sheets"]]}
        if name == "drawing_connections":
            _, matrix = self.selected_graph(args["sheet_id"], args["method"], args["facility_id"])
            matrix["scope"]["method"] = args["method"]
            return {key: matrix[key] for key in ("scope", "nodes", "direct_edges", "warnings")}
        return super().tool(name, args)


class Handler(BaseHandler):
    server_version = "AIPV3/" + VERSION
    app_version = VERSION
    read_tools = READ_TOOLS + [{"name": "list_drawings", "description": "List local drawings, IDs and available extractions.",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
        {"name": "drawing_connections",
        "description": "Read equipment and direct connections with source evidence from one saved drawing extraction.",
        "inputSchema": {"type": "object", "properties": {"facility_id": {"type": "string"},
                        "sheet_id": {"type": "string"}, "method": {"type": "string", "enum": ["v2", "v3"]}},
                        "required": ["facility_id", "sheet_id", "method"], "additionalProperties": False}}]

    def extra_get(self, path, query):
        if path == "/v3.js":
            self.send(Path(__file__).with_name("static").joinpath("v3.js").read_bytes(),
                      content_type="text/javascript; charset=utf-8")
        elif path == "/api/v3/providers":
            self.send({"providers": self.app.list_profiles()})
        elif path == "/api/v3/client-config":
            self.send({"mcpServers": {"aip-drawings": {"command": sys.executable,
                       "args": [str(ROOT / "scripts/aip_mcp.py"), "--port", str(self.server.server_port)]}}})
        elif path == "/api/v3/trace":
            self.send({"trace": self.app.latest(query.get("sheet_id", [None])[0])})
        elif path == "/api/v3/report":
            names = ("v3_pid2graph_comparison.json", "v3_comparison.json", "v3_synthetic_comparison.json")
            reports = [json.loads((ROOT / "reports" / name).read_text(encoding="utf-8")) for name in names]
            self.send({"reports": reports})
        else:
            return False
        return True

    def extra_post(self, path, body):
        if path == "/api/v3/plan/sign-in":
            self.send(self.app.plan.sign_in(account=body.get("account")))
        elif path == "/api/v3/plan/sign-out":
            self.send(self.app.plan.sign_out())
        elif path == "/api/v3/connect":
            self.send(self.app.connect_profile(body))
        elif path == "/api/v3/trace":
            self.send({"trace": self.app.trace(body)})
        elif path == "/api/v3/review":
            self.send({"trace": self.app.review_trace(body)})
        elif path == "/api/v3/ask":
            self.send(self.app.ask_selected(body))
        else:
            return False
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data", type=Path, default=ROOT / "runs/v3")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    serve(args.data, args.port, not args.no_browser, handler=Handler, application=Application, version=VERSION)
