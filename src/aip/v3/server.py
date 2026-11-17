"""Serve the V3 drawing workbench and its isolated AI predictions on loopback."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading

from ..io import ROOT
from ..v2.grid import equipment_positions
from ..v2.matrix import build_matrix
from ..v2.providers import provider_status
from ..v2.server import Application as BaseApplication, Handler as BaseHandler, READ_TOOLS, serve
from ..v2.store import Conflict, now
from . import VERSION
from .ask import ask_drawing
from .vision import MODEL_ID, extract_connections


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
        with self.store.db:
            self.store.db.execute("CREATE TABLE IF NOT EXISTS v3_traces "
                                  "(sheet_id TEXT PRIMARY KEY, result TEXT NOT NULL)")

    def state(self, facility_id=None):
        state = super().state(facility_id)
        state["capabilities"] = {"ai_connections": True}
        return state

    def list_profiles(self):
        with self._lock:
            return available_profiles(self._profiles)

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

    def require_profile(self, key, capability):
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
        with self.store.lock:
            row = self.store.db.execute("SELECT result FROM v3_traces WHERE sheet_id=?", (sheet_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def trace(self, body):
        sheet = self.store.sheet(body.get("sheet_id"))
        sheet_id = sheet["id"]
        settings = self.require_profile(body.get("provider"), "trace")
        with self._lock:
            if sheet_id in self._in_flight:
                raise Conflict("A connection trace is already running for this drawing.")
            self._in_flight.add(sheet_id)
        try:
            image = (self.store.directory / "images" / f"{sheet_id}.png").read_bytes()
            trace = extract_connections(settings, image, "image/png", sheet["width"], sheet["height"])
            trace.update(sheet_id=sheet_id, created_at=now())
            encoded = json.dumps(trace, allow_nan=False)
            with self.store.lock, self.store.db:
                self.store.db.execute("INSERT OR REPLACE INTO v3_traces VALUES (?,?)", (sheet_id, encoded))
            return trace
        finally:
            with self._lock:
                self._in_flight.remove(sheet_id)

    def selected_graph(self, sheet_id, method, facility_id=None):
        sheet = self.store.sheet(sheet_id)
        if facility_id is not None and facility_id != sheet["facility_id"]:
            raise ValueError("Drawing is outside the selected collection.")
        if method == "v2":
            return sheet, self.matrix(sheet["facility_id"], sheet_id)
        if method != "v3":
            raise ValueError("Select the V2 or V3 extraction.")
        trace = self.latest(sheet_id)
        if trace is None:
            raise ValueError("Trace this drawing with V3 first.")
        sheet.update(components=trace["components"], connections=trace["connections"])
        state = {"facilities": [{"id": sheet["facility_id"]}], "sheets": [sheet]}
        matrix = build_matrix(state, sheet["facility_id"], sheet_id)
        matrix["scope"].update(method="v3", model=trace["model"], provider=trace["provider"])
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
        return equipment_positions(sheet)

    def ask_selected(self, body):
        settings = self.require_profile(body.get("provider"), "ask")
        sheet, matrix = self.selected_graph(body.get("sheet_id"), body.get("method", "v2"))
        return ask_drawing(settings, body.get("question"), sheet, matrix)

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
        if path == "/api/v3/connect":
            self.send(self.app.connect_profile(body))
        elif path == "/api/v3/trace":
            self.send({"trace": self.app.trace(body)})
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
