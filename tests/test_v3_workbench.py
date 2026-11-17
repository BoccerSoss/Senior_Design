"""Verify V3 persistence, source selection, session credentials and local client transport."""
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw

from aip.io import ROOT
from aip.v2.providers import _config
from aip.v3 import VERSION
from aip.v3.server import Application, Handler


class V3Workbench(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        plan = patch("aip.v3.server.PlanClient", autospec=True)
        self.plan = plan.start().return_value
        self.addCleanup(plan.stop)
        self.plan.status.return_value = {"supported": True, "signed_in": False, "ready": False, "error": "",
                                         "pending": False, "model": "", "models": []}
        self.app = Application(self.temp.name)
        self.sheet_id = "drawing"
        self.sheet = {"id": self.sheet_id, "facility_id": "facility-local", "width": 50, "height": 50,
                      "title": "Test", "drawing_number": "T", "revision": "A", "sheet_number": "1",
                      "components": [], "connections": [], "source_metadata": {}}
        self.app.store.add_sheet(self.sheet)
        directory = Path(self.temp.name) / "images"
        directory.mkdir(exist_ok=True)
        (directory / "drawing.png").write_bytes(b"test-raster")
        self.graph = {"provider": "local-ml", "model": "model", "excluded_connections": [],
                      "components": [{"id": "a", "tag": "A", "type": "pump", "bbox": [2, 4, 10, 10]},
                                     {"id": "b", "tag": "B", "type": "valve", "bbox": [22, 4, 30, 10]}],
                      "connections": [{"id": "edge-1", "source": "a", "target": "b", "kind": "pipe",
                                       "path": [[10, 7], [22, 7]], "direction": "unknown"}]}

    def tearDown(self):
        self.app.imports.executor.shutdown()
        self.app.store.db.close()
        self.temp.cleanup()

    def trace(self):
        with patch("aip.v3.server.extract_connections", return_value=self.graph):
            return self.app.trace({"sheet_id": self.sheet_id, "provider": "local-ml"})

    def test_selected_source_drives_ask_grid_matrix_and_both_exports(self):
        self.assertFalse(self.app.store.snapshot()["settings"]["use_geometry_reference"])
        self.trace()
        query = {"sheet_id": ["drawing"], "method": ["v3"]}
        candidate = self.app.matrix_query(query)
        self.assertEqual([[0, 1], [1, 0]], candidate["matrix"])
        self.assertEqual([], self.app.matrix_query({"sheet_id": ["drawing"]})["matrix"])
        self.assertEqual(candidate, json.loads(self.app.export({**query, "format": ["matrix-json"]})[0]))
        grid = self.app.grid_query(query)
        self.assertEqual([[1, 1], [2, 1]], [item["coordinate"] for item in grid["coordinates"]])
        self.assertEqual(grid, json.loads(self.app.export({**query, "format": ["grid"]})[0]))
        answer = self.app.ask_selected({"provider": "local-graph", "method": "v3", "sheet_id": "drawing",
                                        "question": "What connects to A?"})
        self.assertEqual(["edge-1"], [item["id"] for item in answer["citations"]])
        self.assertEqual(["v2", "v3"], self.app.tool("list_drawings", {})["drawings"][0]["methods"])
        with self.assertRaises(ValueError):
            self.app.tool("drawing_connections", {"sheet_id": "drawing", "facility_id": "other", "method": "v3"})

    def test_failed_retry_preserves_previous_successful_trace_and_v2(self):
        self.assertIsNone(self.app.latest("drawing"))
        expected = self.trace()
        with patch("aip.v3.server.extract_connections", side_effect=ValueError("Invalid response")):
            with self.assertRaisesRegex(ValueError, "Invalid response"):
                self.app.trace({"sheet_id": "drawing", "provider": "local-ml"})
        self.assertEqual(expected, self.app.latest("drawing"))
        self.assertEqual([], self.app.store.sheet("drawing")["components"])
        self.assertEqual(self.trace(), self.app.latest("drawing"))

    def test_session_key_is_private_and_gateway_cannot_receive_provider_key(self):
        fake = "session-key-for-test"
        with patch.dict(os.environ, {"AIP_GATEWAY_MODEL": "local", "AIP_GATEWAY_BASE_URL": "https://gateway.example/v1",
                                     "OPENAI_API_KEY": "another-key"}, clear=True):
            self.app.connect_profile({"provider": "openai", "model": "chosen-model", "api_key": fake})
            self.assertTrue(next(p["ready"] for p in self.app.list_profiles() if p["id"] == "openai"))
            self.assertNotIn(fake, json.dumps([self.app.list_profiles(), self.app.store.snapshot(private=True)]))
            self.assertEqual(fake, _config(self.app.require_profile("openai", "ask"))["_key"])
            self.assertEqual("", _config(self.app.require_profile("compatible", "ask"))["_key"])
            with self.assertRaises(ValueError):
                self.app.connect_profile({"provider": "compatible", "model": "x", "api_key": fake})
            with self.assertRaises(ValueError):
                self.app.connect_profile({"provider": "openai", "model": "x", "api_key": "bad\nheader"})

    def test_subscription_trace_and_question_share_the_selected_graph_without_an_api_key(self):
        request = {"provider": "chatgpt-plan", "sheet_id": self.sheet_id, "model": "selected"}
        self.app.connect_profile({"provider": "openai", "model": "api-model", "api_key": "test-key"})
        with self.assertRaisesRegex(ValueError, "Continue with ChatGPT"):
            self.app.trace(request)
        self.plan.generate.assert_not_called()
        self.plan.status.return_value.update(ready=True, signed_in=True, model="first", models=[
            {"slug": slug, "display_name": slug} for slug in ("first", "selected")])
        with self.assertRaisesRegex(ValueError, "Select a model"):
            self.app.trace({**request, "model": "api-model"})
        image = Image.new("RGB", (50, 50), "white")
        ImageDraw.Draw(image).line([(10, 7), (22, 7)], fill="black")
        image.save(Path(self.temp.name) / "images" / "drawing.png")
        self.graph["connections"][0]["evidence"] = "Horizontal ink between both symbol boxes."
        self.plan.generate.side_effect = [json.dumps(self.graph), '{"references":["S1"]}']
        with patch("aip.v2.providers._post", side_effect=AssertionError("unexpected API-key request")):
            trace = self.app.trace(request)
            answer = self.app.ask_selected({**request, "question": "How many connections?", "method": "v3"})
        self.assertEqual(("chatgpt-plan", "selected", 1), (trace["provider"], trace["model"], len(trace["connections"])))
        self.assertEqual("selected", answer["model"])
        self.assertEqual(self.sheet_id, answer["citations"][0]["sheet_id"])
        self.assertIn("1 direct equipment connections", answer["answer"])
        image_call, graph_call = self.plan.generate.call_args_list
        self.assertTrue(image_call.args[2].startswith("data:image/png;base64,"))
        self.assertIsNone(graph_call.args[2])
        self.assertEqual("selected", graph_call.kwargs["model"])

    def test_external_client_stdio_negotiates_and_receives_candidate_edges(self):
        self.trace()
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            server.app = self.app
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def req(n, method, params):
                return json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params})
            messages = [req(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "test", "version": "1"}}),
                        req(2, "tools/list", {}), req(3, "tools/call", {"name": "list_drawings", "arguments": {}}),
                        req(4, "tools/call", {"name": "drawing_connections", "arguments": {
                            "facility_id": "facility-local", "sheet_id": "drawing", "method": "v3"}})]
            try:
                result = subprocess.run([sys.executable, str(ROOT / "scripts/aip_mcp.py"), "--port", str(server.server_port)],
                                        input="\n".join(messages) + "\n", capture_output=True, text=True, check=True, timeout=12)
            finally:
                server.shutdown()
                thread.join(timeout=2)
        replies = [json.loads(line)["result"] for line in result.stdout.splitlines()]
        self.assertEqual(VERSION, replies[0]["serverInfo"]["version"])
        self.assertIn("drawing_connections", [tool["name"] for tool in replies[1]["tools"]])
        self.assertEqual("drawing", replies[2]["structuredContent"]["drawings"][0]["id"])
        self.assertEqual("edge-1", replies[3]["structuredContent"]["direct_edges"][0]["observations"][0]["connection_id"])
