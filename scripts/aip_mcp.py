"""Connect an MCP stdio client to the running local drawing workbench."""
import argparse
import json
import sys
from urllib import error, request


def relay(port, source, target):
    address = f"http://127.0.0.1:{port}"
    # Use direct loopback even when a corporate HTTP proxy is configured.
    transport = request.build_opener(request.ProxyHandler({}), NoRedirect())
    key = None
    version = "2025-03-26"
    for line in source:
        if not line.strip():
            continue
        message = None
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("Expected a JSON-RPC object.")
            if key is None:
                with transport.open(address + "/api/integration", timeout=5) as response:
                    key = json.load(response)["api_key"]
            headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                       "Authorization": "Bearer " + key, "MCP-Protocol-Version": version}
            req = request.Request(address + "/mcp", data=line.encode("utf-8"), headers=headers, method="POST")
            try:
                response = transport.open(req, timeout=35)
            except error.HTTPError as exc:
                if exc.code == 401:
                    key = None
                response = exc
            with response:
                data = response.read()
            if not data:
                continue
            reply = json.loads(data)
            if reply.get("jsonrpc") != "2.0":
                raise ValueError("Workbench could not process the request.")
            if message.get("method") == "initialize" and "result" in reply:
                version = reply["result"]["protocolVersion"]
        except (ValueError, KeyError, OSError):
            if isinstance(message, dict) and "id" not in message:
                continue
            reply = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                     "error": {"code": -32000, "message": "Open AIP V3 and check the configured local port."}}
        target.write(json.dumps(reply, ensure_ascii=True) + "\n")
        target.flush()


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding="utf-8")
    relay(args.port, sys.stdin, sys.stdout)
