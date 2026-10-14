"""Smoke test: start the app in mock mode (no models, no network) and poke REST, MCP and the UI.

Usage: python scripts/smoke.py        (exit code 0 when everything answers)
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Any

MCP_TOOLS = {
    "room_status",
    "list_speakers",
    "who_is_speaking",
    "recent_turns",
    "wait_for_next_turn",
    "speak",
    "set_response_mode",
    "name_speaker",
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def request(url: str, body: dict[str, Any] | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    req = urllib.request.Request(url, data=data, headers=headers if data else {})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, response.read()


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data_dir = tempfile.mkdtemp(prefix="utterlane-smoke-")  # never the user's real data
    env = {k: v for k, v in os.environ.items() if not k.startswith("UTTERLANE_")}
    env.pop("OPENAI_API_KEY", None)
    env["UTTERLANE_DATA_DIR"] = data_dir
    command = [sys.executable, "-m", "utterlane_live", "--port", str(port), "run", "--mock"]
    server = subprocess.Popen([*command, "--no-browser"], env=env, cwd=data_dir)
    try:
        deadline = time.time() + 120
        while True:
            try:
                status, raw = request(f"{base}/api/health")
                health = json.loads(raw)
                if status == 200 and health.get("status") != "loading":
                    break
            except OSError:
                pass
            if server.poll() is not None or time.time() > deadline:
                print("FAIL: the app did not start", file=sys.stderr)
                return 1
            time.sleep(0.5)
        print(f"health: {health.get('status')} - {health.get('hardware')}")

        _, raw = request(f"{base}/api/state")
        state = json.loads(raw)
        assert state["mode"] == "smart", state

        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        _, raw = request(f"{base}/mcp", call)
        names = {tool["name"] for tool in json.loads(raw)["result"]["tools"]}
        assert names == MCP_TOOLS, names
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "room_status", "arguments": {}},
        }
        _, raw = request(f"{base}/mcp", call)
        assert json.loads(raw)["result"]["structuredContent"]["mode"] == "smart"
        print(f"mcp: {len(names)} tools")

        status, raw = request(f"{base}/")
        assert status == 200 and b"<html" in raw.lower(), raw[:200]
        print("ui:", "built" if b"/assets/" in raw else "placeholder page (web UI not built)")
        print("OK")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=15)
        for _ in range(40):  # Windows may hold the folder for a moment after exit
            shutil.rmtree(data_dir, ignore_errors=True)
            if not os.path.exists(data_dir):
                break
            time.sleep(0.25)


if __name__ == "__main__":
    sys.exit(main())
