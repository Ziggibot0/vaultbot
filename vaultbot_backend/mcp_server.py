"""
VaultBot MCP server (stdio transport) — self-contained, no `mcp` package.

Implements the Model Context Protocol over stdio using only the Python
standard library + `requests` (both already in the vault venv). This means
the plugin can spawn it with zero extra pip installs — it just works when
Obsidian opens.

Protocol: JSON-RPC 2.0 over stdin/stdout, per the MCP specification.
  - initialize / initialized handshake
  - tools/list  → returns tool schemas
  - tools/call  → dispatches to the backend and returns the result

The server is a thin shim: all heavy lifting (web search, extractive
synthesis, note writing, vault graph) lives in the backend HTTP API.
The burden stays on the vault/web, not on the LLM's weights.

Tool surfaces
-------------
VaultBot exposes tools through two paths with different audiences:

**In-vault chat (Obsidian plugin → /ws)**
    The chat LLM sees ALL tools: 4 builtin vault tools (vault_research,
    vault_search, vault_gaps, vaultbot_status) + 7 meta/self-improvement
    tools (code_read, code_run, tool_create, self_reflect, git_rollback,
    safe_write, capability_audit) + N agent-authored custom tools. This
    is the full-featured surface — the agent can research, self-improve,
    and use any tool it has written for itself.

**External MCP (Copilot Chat, Claude Desktop → this stdio server)**
    MCP clients see a curated surface: 3 vault tools (vault_research,
    vault_gaps, vaultbot_status) + N agent-authored custom tools. The
    self-improvement meta-tools (code_read, code_run, safe_write, etc.)
    are NOT exposed — they're for the in-vault agent's autonomous growth,
    not for external clients. This is by design: an external MCP client
    asking "research X" should get research, not the ability to rewrite
    the backend's source code.
"""

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import requests

BACKEND_URL = os.getenv("VAULTBOT_BACKEND_URL", "http://localhost:8000")
RESEARCH_TIMEOUT = int(os.getenv("VAULTBOT_RESEARCH_TIMEOUT", "180"))

# The shared-secret auth token lives next to the backend (vaultbot_backend/
# .vaultbot_auth_token). The MCP server is spawned by the plugin and is a
# trusted internal caller, so it reads the token and attaches it to mutating
# requests (POST /research_tool, POST /custom_tools/call) — which now require
# auth even from localhost (issue #230).
_TOKEN_FILE = Path(__file__).resolve().parent / ".vaultbot_auth_token"


def _auth_token() -> str:
    """Read the backend's shared-secret token ("" if absent/corrupt)."""
    try:
        token = _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
        return token
    return ""


def _auth_headers() -> dict[str, str]:
    """Headers for mutating backend calls, including the token when present."""
    headers: dict[str, str] = {}
    token = _auth_token()
    if token:
        headers["X-VaultBot-Token"] = token
    return headers


def _backend_research(topic: str, depth: str = "deep") -> dict[str, Any]:
    try:
        resp = requests.post(
            f"{BACKEND_URL}/research_tool",
            json={"topic": topic, "depth": depth},
            headers=_auth_headers(),
            timeout=RESEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": f"backend unreachable: {e}", "topic": topic}


def _backend_status() -> dict[str, Any]:
    try:
        resp = requests.get(f"{BACKEND_URL}/autonomous/status", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": f"backend unreachable: {e}"}


def _backend_gaps() -> dict[str, Any]:
    try:
        resp = requests.get(f"{BACKEND_URL}/autonomous/gaps", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": f"backend unreachable: {e}"}


def _backend_custom_tools() -> dict[str, Any]:
    try:
        resp = requests.get(f"{BACKEND_URL}/custom_tools", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": f"backend unreachable: {e}"}


def _backend_call_custom_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = requests.post(
            f"{BACKEND_URL}/custom_tools/call",
            json={"name": name, "args": args},
            headers=_auth_headers(),
            timeout=RESEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": f"backend unreachable: {e}"}


# Tool definitions (MCP tool schemas)
TOOLS = [
    {
        "name": "vault_research",
        "description": (
            "Deep-research a topic the assistant doesn't know enough about, "
            "using the VaultBot backend's web research engine. Returns a "
            "sourced, corroborated summary and writes a linked research note "
            "into the vault. Use this whenever the vault's notes are thin or "
            "the LLM detects a knowledge gap. The burden is on the vault/web, "
            "not the model's weights."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "The topic or question to research. Be specific — "
                        "the engine digs until coverage plateaus."
                    ),
                },
                "depth": {
                    "type": "string",
                    "enum": ["deep", "quick"],
                    "default": "deep",
                    "description": (
                        "'deep' runs multiple search rounds + gap fills; "
                        "'quick' is a single round for fast lookups."
                    ),
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "vault_gaps",
        "description": (
            "List the vault's own knowledge gaps: dangling wikilinks (red "
            "links the vault declared it wants) and thin notes. Use this to "
            "decide what to research next."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "vaultbot_status",
        "description": (
            "Report whether the VaultBot backend and its autonomous "
            "researcher are running, plus recent autonomous research "
            "history."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run a tool call and return {isError, text}."""
    if name == "vault_research":
        topic = (args.get("topic") or "").strip()
        depth = args.get("depth", "deep")
        if not topic:
            return {"isError": True, "text": "Error: `topic` is required."}
        result = _backend_research(topic, depth)
        return {"isError": False, "text": _format_research_result(result)}
    if name == "vault_gaps":
        result = _backend_gaps()
        return {"isError": False, "text": _format_gaps(result)}
    if name == "vaultbot_status":
        result = _backend_status()
        return {"isError": False, "text": json.dumps(result, indent=2, default=str)}
    # Custom (agent-authored) tools: dispatch via the backend.
    result = _backend_call_custom_tool(name, args)
    if isinstance(result, dict) and result.get("error"):
        return {
            "isError": True,
            "text": f"Custom tool '{name}' error: {result['error']}",
        }
    return {"isError": False, "text": json.dumps(result, indent=2, default=str)}


# Minimal MCP stdio server (JSON-RPC 2.0) — no external deps -------------
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "vaultbot-research", "version": "1.0.0"}
CAPABILITIES = {"tools": {}}
_NOTIFICATIONS = {"notifications/initialized"}
_initialized = threading.Event()


def _make_response(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _make_error(
    msg_id: Any, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _handle_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    msg_id = msg.get("id")
    method = msg.get("method", "")
    if msg_id is None or method in _NOTIFICATIONS:
        if method == "notifications/initialized":
            _initialized.set()
        return None
    params = msg.get("params") or {}
    if method == "initialize":
        return _make_response(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": CAPABILITIES,
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "tools/list":
        # Fetch agent-authored custom tools from the backend and merge them
        # with the built-in tools so external clients see everything.
        tools = list(TOOLS)
        ct = _backend_custom_tools()
        if isinstance(ct, dict) and not ct.get("error"):
            for t in ct.get("tools", []):
                fn = t.get("function", {})
                if fn.get("name"):
                    tools.append(
                        {
                            "name": fn["name"],
                            "description": fn.get("description", ""),
                            "inputSchema": fn.get(
                                "parameters", {"type": "object", "properties": {}}
                            ),
                        }
                    )
        return _make_response(msg_id, {"tools": tools})
    if method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments") or {}
        res = _dispatch_tool(tool_name, args)
        content = [{"type": "text", "text": res["text"]}]
        if res.get("isError"):
            return _make_response(msg_id, {"content": content, "isError": True})
        return _make_response(msg_id, {"content": content})
    if method == "ping":
        return _make_response(msg_id, {})
    if method == "resources/list":
        return _make_response(msg_id, {"resources": []})
    if method == "prompts/list":
        return _make_response(msg_id, {"prompts": []})
    return _make_error(msg_id, -32601, f"Method not found: {method}")


def _write_stdout(obj: dict[str, Any]) -> None:
    try:
        sys.stdout.write(json.dumps(obj, default=str) + "\n")
        sys.stdout.flush()
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        sys.stderr.write(f"[vaultbot-mcp] stdout write failed: {e}\n")


def main():
    sys.stderr.write(f"[vaultbot-mcp] starting, backend={BACKEND_URL}\n")
    try:
        for raw_line in sys.stdin:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                msg = json.loads(raw_line)
            except json.JSONDecodeError:
                _write_stdout(_make_error(None, -32700, "Parse error"))
                continue
            try:
                response = _handle_request(msg)
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                response = _make_error(msg.get("id"), -32603, f"Internal error: {e}")
            if response is not None:
                _write_stdout(response)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    # Clean exit on EOF so MCP clients don't interpret a closed stdin as a crash.
    sys.exit(0)


def _format_research_result(result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"Research failed: {result['error']}"
    lines = [
        f"# Research: {result.get('topic', '?')}",
        "",
        f"Sources: {result.get('source_count', 0)} | "
        f"Facts: {result.get('synthesis_facts', 0)} | "
        f"Rounds: {len(result.get('rounds', []))}",
        "",
        "## Findings",
        result.get("synthesis", "(no synthesis produced)"),
        "",
    ]
    if result.get("note_path"):
        lines += ["## Note written", f"[[{Path(result['note_path']).stem}]]", ""]
    if result.get("gaps_filled"):
        lines += [
            "## Gap-fill queries",
            "\n".join(f"- {g}" for g in result["gaps_filled"]),
            "",
        ]
    if result.get("sources"):
        lines += ["## Sources"]
        lines += [
            f"- [{s.get('title') or s.get('url')}]({s.get('url')})"
            for s in result["sources"][:12]
        ]
    return "\n".join(lines)


def _format_gaps(result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"Could not fetch gaps: {result['error']}"
    gaps: list[dict[str, Any]] = result.get("gaps", [])
    if not gaps:
        return "No knowledge gaps detected — the vault looks complete."
    lines = [f"# Vault knowledge gaps ({len(gaps)})", ""]
    for g in gaps[:20]:
        kind = g.get("kind", "?")
        topic = g.get("topic", "?")
        priority = g.get("priority", 0)
        lines.append(f"- [{kind}] {topic} (priority {priority})")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
