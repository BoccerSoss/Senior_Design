"""Real loopback HTTP checks; all AI transports are deliberately disabled."""
from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from aip.v2.demo import create_demo
from aip.v2.server import Application, Handler


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.temp.name))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.address = f"http://127.0.0.1:{self.server.server_port}"
        self.key = self.app.store.snapshot(private=True)["api_key"]
        self.provider_patch = patch("aip.v2.providers._open_request", side_effect=AssertionError("AI network call in HTTP test"))
        self.provider_transport = self.provider_patch.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.app.imports.executor.shutdown(wait=True, cancel_futures=True)
        self.app.store.db.close()
        self.provider_patch.stop()
        self.temp.cleanup()

    def call(self, method, path, body=None, headers=None):
        options = dict(headers or {})
        if body is not None and not isinstance(body, (bytes, str)):
            body = json.dumps(body).encode("utf-8")
            options.setdefault("Content-Type", "application/json")
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=options)
            response = connection.getresponse()
            content = response.read()
            metadata = dict(response.getheaders())
            value = json.loads(content) if content and metadata.get("Content-Type", "").startswith("application/json") else content
            return response.status, value, metadata
        finally:
            connection.close()

    def rpc(self, method, params=None, request_id=1, headers=None):
        body = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            body["id"] = request_id
        if params is not None:
            body["params"] = params
        options = {"X-API-KEY": self.key, "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-11-25"}
        options.update(headers or {})
        return self.call("POST", "/mcp", body, options)

    def test_default_state_has_no_credentials_and_local_queries_make_no_ai_calls(self):
        status, state, _ = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["sheets"], [])
        self.assertNotIn("api_key", state)
        self.assertEqual(state["provider_status"]["provider"], "local")
        self.assertTrue(state["provider_status"]["ready"])
        status, result, _ = self.call("POST", "/api/action", {"action": "ask", "provider": "local",
            "facility_id": "facility-local", "question": "Find the pump"}, {"Origin": self.address})
        self.assertEqual(status, 200)
        self.assertEqual(result["result"]["provider"], "local")
        self.assertFalse(self.provider_transport.called)

    def local_question(self, demo, question, **options):
        payload = {"action": "ask", "provider": "local", "facility_id": demo["facility_id"],
                   "sheet_id": demo["sheet_ids"][0], "question": question, **options}
        status, body, _ = self.call("POST", "/api/action", payload, {"Origin": self.address})
        self.assertEqual(status, 200, body)
        self.assertFalse(self.provider_transport.called)
        return body["result"]

    def test_local_list_equipment_uses_selected_sheet_even_with_component_selected(self):
        demo = create_demo(self.app.store)
        for question in ("List the equipment on this sheet", "What equipment is on this sheet?"):
            result = self.local_question(demo, question, component_id="p-101")
            self.assertEqual(len(result["matches"]["components"]), 13)
            self.assertTrue(all(item["sheet_id"] == demo["sheet_ids"][0] for item in result["matches"]["components"]))
            self.assertIn("P-101", result["answer"])
            self.assertIn("13 recorded equipment observations", result["answer"])
            self.assertIn("RL-001", result["answer"])
            self.assertNotIn("V-201", result["answer"])
            self.assertEqual([item["sheet_id"] for item in result["citations"]], [demo["sheet_ids"][0]])
            self.assertIsNone(result["trace"])

    def test_local_type_search_and_literal_observed_tag_work_in_natural_questions(self):
        demo = create_demo(self.app.store)
        result = self.local_question(demo, "Show the pumps on this sheet")
        self.assertEqual([item["component_id"] for item in result["matches"]["components"]], ["p-101"])
        self.assertIn("P-101 (pump)", result["answer"])
        result = self.local_question(demo, "Which valves are on this sheet?")
        self.assertEqual({item["component_id"] for item in result["matches"]["components"]},
                         {"v-101", "v-102", "dv-101", "av-101", "rv-101"})
        result = self.local_question(demo, "Find P-101")
        self.assertEqual([item["component_id"] for item in result["matches"]["components"]], ["p-101"])
        self.assertIn("1 recorded equipment observation", result["answer"])
        self.assertIn("P-101 (pump)", result["answer"])
        self.assertIsNone(result["trace"])
        missing = self.local_question(demo, "Find P-101-DOES-NOT-EXIST")
        self.assertEqual(missing["matches"]["components"], [])
        self.assertIn("0 recorded equipment observations", missing["answer"])

    def test_local_unique_tag_infers_trace_and_isolation_target_without_a_model(self):
        demo = create_demo(self.app.store)
        traced = self.local_question(demo, "What connects to P-101?")
        self.assertIsNotNone(traced["trace"])
        self.assertIn("P-101", traced["answer"])
        self.assertTrue(any(item["sheet_id"] == demo["sheet_ids"][1] for item in traced["trace"]["nodes"]))
        self.assertEqual({item["sheet_id"] for item in traced["citations"]}, set(demo["sheet_ids"]))
        boundary = self.local_question(demo, "What is the candidate isolation boundary for P-101?")
        self.assertEqual({item["component_id"] for item in boundary["isolation"]["closures"]},
                         {"v-101", "v-102", "v-201", "v-202"})
        self.assertIn("4 closure candidates", boundary["answer"])
        self.assertIn("RL-002", boundary["answer"])

    def test_local_scope_and_duplicate_tag_ambiguity_prevent_target_guessing(self):
        demo = create_demo(self.app.store)
        duplicate = self.app.store.sheet(demo["sheet_ids"][0])
        duplicate.update(id="duplicate-sheet", source_sha256="unit-duplicate", title="Duplicate tag exercise", drawing_number="DUP-1")
        self.app.store.add_sheet(duplicate)
        ambiguous = self.local_question(demo, "Trace P-101", sheet_id=None)
        self.assertEqual(len(ambiguous["matches"]["components"]), 2)
        self.assertIsNone(ambiguous["trace"])
        self.assertIn("Select one component", ambiguous["answer"])
        scoped = self.local_question(demo, "Trace P-101")
        self.assertIsNotNone(scoped["trace"])
        self.assertNotIn("duplicate-sheet", {item["sheet_id"] for item in scoped["trace"]["nodes"]})
        other = self.app.store.add_facility("Another facility")
        status, _, _ = self.call("POST", "/api/action", {"action": "ask", "provider": "local",
            "facility_id": other["id"], "sheet_id": demo["sheet_ids"][0], "question": "List equipment"})
        self.assertEqual(status, 400)
        self.assertFalse(self.provider_transport.called)

    def test_origin_host_and_api_key_checks_reject_cross_origin_access(self):
        status, _, _ = self.call("GET", "/api/state", headers={"Origin": "https://untrusted.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.call("GET", "/api/state", headers={"Host": "untrusted.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.call("POST", "/api/action", {"action": "create_facility", "name": "should not exist"},
                                 {"Origin": "https://untrusted.example"})
        self.assertEqual(status, 403)
        self.assertEqual(len(self.app.store.snapshot()["facilities"]), 1)
        for key in (None, "wrong-key", "caf\u00e9"):
            options = {"Accept": "application/json, text/event-stream"}
            if key is not None:
                options["X-API-KEY"] = key
            status, _, _ = self.call("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, options)
            self.assertEqual(status, 401)
        status, _, _ = self.call("GET", "/api/v1/docs")
        self.assertEqual(status, 401)
        status, _, _ = self.call("GET", "/api/integration", headers={"Origin": "https://untrusted.example"})
        self.assertEqual(status, 403)

    def test_mcp_initialization_negotiates_supported_and_future_versions(self):
        for version in ("2025-11-25", "2025-06-18", "2025-03-26", "future-version"):
            status, body, headers = self.rpc("initialize", {"protocolVersion": version, "capabilities": {},
                "clientInfo": {"name": "unit-client", "version": "1"}})
            self.assertEqual(status, 200)
            self.assertEqual(body["jsonrpc"], "2.0")
            self.assertEqual(body["id"], 1)
            self.assertEqual(body["result"]["protocolVersion"], version if version != "future-version" else "2025-11-25")
            self.assertNotIn("MCP-Session-Id", headers)
        status, body, _ = self.rpc("initialize", {})
        self.assertEqual(body["error"]["code"], -32602)

    def test_mcp_notifications_and_client_responses_return_202_without_a_body(self):
        for method in ("notifications/initialized", "notifications/cancelled", "notifications/progress", "future/notification"):
            status, body, _ = self.rpc(method, {"requestId": 42}, request_id=None)
            self.assertEqual((status, body), (202, b""))
        options = {"X-API-KEY": self.key, "Accept": "application/json, text/event-stream"}
        status, body, _ = self.call("POST", "/mcp", {"jsonrpc": "2.0", "id": "reply-1", "result": {}}, options)
        self.assertEqual((status, body), (202, b""))

    def test_march_2025_batch_compatibility_and_modern_batch_rejection(self):
        options = {"X-API-KEY": self.key, "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-03-26"}
        batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        status, body, _ = self.call("POST", "/mcp", batch, options)
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in body], [1, 2])
        status, body, _ = self.call("POST", "/mcp", [batch[1]], options)
        self.assertEqual((status, body), (202, b""))
        status, body, _ = self.call("POST", "/mcp", batch, {**options, "MCP-Protocol-Version": "2025-11-25"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], -32600)

    def test_mcp_stateless_get_delete_and_protocol_header(self):
        options = {"X-API-KEY": self.key, "Accept": "text/event-stream"}
        for method in ("GET", "DELETE"):
            status, _, headers = self.call(method, "/mcp", headers=options)
            self.assertEqual(status, 405)
            self.assertIn("POST", headers["Allow"])
            status, _, _ = self.call(method, "/mcp", headers={**options, "Origin": "https://untrusted.example"})
            self.assertEqual(status, 403)
        status, _, _ = self.rpc("ping", headers={"MCP-Protocol-Version": "unsupported"})
        self.assertEqual(status, 400)
        for method in ("GET", "DELETE"):
            status, _, _ = self.call(method, "/mcp", headers={**options, "MCP-Protocol-Version": "unsupported"})
            self.assertEqual(status, 400)

    def test_mcp_envelope_and_tool_argument_validation(self):
        options = {"X-API-KEY": self.key, "Accept": "application/json, text/event-stream"}
        for value in ([], {"id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": True, "method": "ping"},
                      {"jsonrpc": "2.0", "id": None, "method": "ping"}, {"jsonrpc": "2.0", "id": 1, "method": []}):
            status, body, _ = self.call("POST", "/mcp", value, options)
            self.assertEqual(status, 400)
            self.assertEqual(body["error"]["code"], -32600)
        status, body, _ = self.rpc("unknown/method")
        self.assertEqual(body["error"]["code"], -32601)
        for params in ({"name": "unknown-tool", "arguments": {}}, {"name": "search_drawings", "arguments": {}},
                       {"name": "search_drawings", "arguments": {"facility_id": "facility-local", "query": 1}},
                       {"name": "search_drawings", "arguments": {"facility_id": "facility-local", "query": "", "extra": 1}}):
            status, body, _ = self.rpc("tools/call", params)
            self.assertEqual(body["error"]["code"], -32602)
        status, body, _ = self.rpc("tools/call", {"name": "find_component", "arguments": {
            "facility_id": "facility-local", "sheet_id": "absent-sheet", "component_id": "absent"}})
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])

    def test_invalid_json_and_mcp_content_negotiation(self):
        options = {"X-API-KEY": self.key, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        for content in (b"not-json-private-data", b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}', b"\xff"):
            status, body, _ = self.call("POST", "/mcp", content, options)
            self.assertEqual(status, 400)
            self.assertEqual(body["error"]["code"], -32700)
            self.assertNotIn("private-data", json.dumps(body))
        status, _, _ = self.rpc("ping", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _, _ = self.rpc("ping", headers={"Accept": "image/png"})
        self.assertEqual(status, 406)

    def test_seeded_demo_traces_confirmed_continuation_and_excludes_signal(self):
        demo = create_demo(self.app.store)
        args = {"facility_id": demo["facility_id"], **demo["target"]}
        status, body, _ = self.rpc("tools/list")
        self.assertEqual(status, 200)
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in body["result"]["tools"]))
        status, body, _ = self.rpc("tools/call", {"name": "trace_lines", "arguments": args})
        self.assertEqual(status, 200)
        trace = body["result"]["structuredContent"]
        self.assertFalse(body["result"]["isError"])
        self.assertTrue(any(item["sheet_id"] == demo["sheet_ids"][1] for item in trace["nodes"]))
        self.assertFalse(any(item["component_id"].startswith("pi-") for item in trace["nodes"]))
        status, body, _ = self.call("POST", "/api/v1/tools/isolation_across_sheets", args, {"X-API-KEY": self.key})
        self.assertEqual(status, 200)
        self.assertEqual({item["component_id"] for item in body["closures"]}, {"v-101", "v-102", "v-201", "v-202"})
        self.assertFalse(any(item["component_id"] == "cv-201" for item in body["closures"]))
        self.assertFalse(self.provider_transport.called)

    def test_grid_export_uses_integer_intersections_independently_of_connection_topology(self):
        demo = create_demo(self.app.store)
        query = urlencode({"sheet_id": demo["sheet_ids"][0], "format": "grid"})
        status, first, _ = self.call("GET", "/api/export?" + query)
        self.assertEqual(status, 200)
        coordinates = {item["component_id"]: item["coordinate"] for item in first["coordinates"]}
        # A component is exactly on its indexed source-center axes.
        self.assertEqual(coordinates["p-101"], [first["axes"]["x"].index(550)+1,
                                               first["axes"]["y"].index(410)+1])
        status, live, _ = self.call("GET", "/api/grid?sheet_id=" + demo["sheet_ids"][0])
        self.assertEqual(status, 200)
        self.assertEqual(first, live)
        self.assertEqual("source-intersections", first["layout_kind"])
        for layout in ("regular", "source"):
            selected = query + "&layout=" + layout
            status, grid, _ = self.call("GET", "/api/grid?" + selected)
            self.assertEqual(200, status)
            self.assertEqual(layout + "-intersections", grid["layout_kind"])
            status, exported, _ = self.call("GET", "/api/export?" + selected)
            self.assertEqual(200, status)
            self.assertEqual(grid, exported)
        status, _, _ = self.call("GET", "/api/grid?" + query + "&layout=other")
        self.assertEqual(400, status)
        for item in first["coordinates"]:
            col, row = item["coordinate"]
            self.assertEqual([first["axes"]["x"][col-1], first["axes"]["y"][row-1]], item["display_point"])
            self.assertEqual(item["source_anchor"], item["display_point"])
        def remove_connections(state):
            next(sheet for sheet in state["sheets"] if sheet["id"] == demo["sheet_ids"][0])["connections"] = []
        self.app.store.mutate(remove_connections)
        status, second, _ = self.call("GET", "/api/export?" + query)
        self.assertEqual(first, second)
        self.assertFalse(self.provider_transport.called)


if __name__ == "__main__":
    unittest.main()
