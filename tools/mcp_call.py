"""Minimal stdio client for exercising the MCP server without an agent.

Usage:
    python tools/mcp_call.py print_list_printers
    python tools/mcp_call.py print_folder '{"name":"...", "folder":"..."}'

Each argument is sent as one `tools/call` request over the real stdio transport,
so this exercises the same code path an MCP client would.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def call(method: str, params: dict | None, request_id: int) -> dict:
    proc = subprocess.Popen(
        [sys.executable, "-m", "print_mcp.server"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    out, err = proc.communicate(json.dumps(payload) + "\n", timeout=180)
    if err.strip():
        print("--- server stderr ---", file=sys.stderr)
        print(err, file=sys.stderr)
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == request_id:
            return msg
    raise SystemExit(f"no response for {method}; server said:\n{out}\n{err}")


def load_arguments(raw: str) -> dict:
    """Parse the arguments argument, which may be inline JSON or @file.json.

    Inline JSON is awkward to pass through PowerShell, which strips the quotes, so
    a file reference is supported.
    """
    raw = raw.strip()
    if raw.startswith("@"):
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
    return json.loads(raw)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if sys.argv[1] == "--list":
        result = call("tools/list", {}, 1)["result"]
        for t in result["tools"]:
            required = ",".join(t["inputSchema"].get("required", []))
            print(f"{t['name']:<26} required=[{required}]")
            print(f"{'':<26} {t['description'][:100]}")
        return 0

    name = sys.argv[1]
    arguments = load_arguments(sys.argv[2]) if len(sys.argv) > 2 else {}
    response = call("tools/call", {"name": name, "arguments": arguments}, 2)
    if "error" in response:
        print(json.dumps(response["error"], indent=2, ensure_ascii=False))
        return 1
    result = response["result"]
    for item in result.get("content", []):
        print(item.get("text", ""))
    if result.get("isError"):
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
