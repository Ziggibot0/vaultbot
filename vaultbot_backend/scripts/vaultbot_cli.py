#!/usr/bin/env python3
"""vaultbot_cli — a scriptable command-line client for the VaultBot backend.

Why this exists
---------------
The backend has no CLI — only an HTTP API on 127.0.0.1:8000 and the Obsidian
plugin GUI, which talks to it over a WebSocket at /ws. That makes the backend
hard to exercise, diagnose, and dogfood without a browser. This client mirrors
the plugin's protocol so an agent or a human can drive the backend from a
terminal and script tests against it.

Protocol
--------
- REST: read-only GETs are trusted from localhost (no token). Any mutating
  method (POST/PUT/DELETE/PATCH) requires the shared secret in the header
  ``X-VaultBot-Token`` (see vaultbot_backend/auth.py). The token lives in
  vaultbot_backend/.vaultbot_auth_token and is read automatically.
- Chat: a WebSocket at /ws. Send ``{"type":"chat","message":"..."}``. The
  backend streams ``answer_chunk`` / ``thinking`` / ``heartbeat`` / ``progress``
  events and sends the final ``answer_done``. A ``problem`` event means the
  turn failed (see chat_handler.py, chat_loop_streaming.py).

Usage
-----
    vaultbot_cli.py health                    # GET /health
    vaultbot_cli.py chat "your message"       # WS chat, print streamed answer
    vaultbot_cli.py chat "hi" --new           # fresh session (clears history)
    vaultbot_cli.py sessions                  # list sessions
    vaultbot_cli.py config                    # GET /config/effective
    vaultbot_cli.py models                    # GET /llm/models/all (pot + roles)
    vaultbot_cli.py roles                     # same, roles only
    vaultbot_cli.py tools                     # GET /custom_tools
    vaultbot_cli.py tool-call NAME '{...}'    # POST /custom_tools/call
    vaultbot_cli.py identity                  # GET /identity
    vaultbot_cli.py ws --json                 # raw WS: stream every event as JSON

Exit codes:
    0  success (health ok / chat answered / read returned)
    1  backend unreachable or auth refused
    2  chat failed (problem event) or returned no answer
    4  usage error (bad args)

All read commands support --json for machine-consumable output. The chat
command prints the streamed answer to stdout and the final text to stderr
prefixed by ``ANSWER:`` so scripts can pipe the body while still capturing
the verdict.

Environment overrides:
    VAULTBOT_BASE   base URL (default http://127.0.0.1:8000)
    VAULTBOT_TOKEN  explicit auth token (else read from the auth file)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import requests
import websockets

DEFAULT_BASE = "http://127.0.0.1:8000"
# Path to the shared-secret token file, relative to this script's module tree.
_TOKEN_FILE = Path(__file__).resolve().parent.parent / ".vaultbot_auth_token"


def _base() -> str:
    return os.environ.get("VAULTBOT_BASE", DEFAULT_BASE).rstrip("/")


def _token() -> str | None:
    env = os.environ.get("VAULTBOT_TOKEN", "").strip()
    if env:
        return env
    try:
        if _TOKEN_FILE.exists():
            tok = _TOKEN_FILE.read_text(encoding="utf-8").strip()
            if tok:
                return tok
    except OSError:
        pass
    return None


class ApiError(RuntimeError):
    """Backend reached but returned a non-2xx or an error payload."""


def _request(method: str, path: str, *, json_body: dict | None = None):
    url = _base() + path
    headers = {}
    if method.upper() in ("POST", "PUT", "DELETE", "PATCH"):
        headers["X-VaultBot-Token"] = _token() or ""  # auth middleware rejects empty
        if json_body is not None:
            headers["Content-Type"] = "application/json"
    try:
        resp = requests.request(
            method,
            url,
            headers=headers,
            json=json_body if method.upper() != "GET" else None,
            timeout=15,
        )
    except requests.RequestException as e:
        raise ApiError(f"unreachable: {e}") from e
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:  # noqa: BLE001 - fall back to raw text
            detail = resp.text
        raise ApiError(f"HTTP {resp.status_code}: {detail}")
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - non-JSON body is still a success
        return {"status": "ok", "text": resp.text}


def _print_json(obj):
    print(json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_health(_args):
    data = _request("GET", "/health")
    ok = bool(data.get("ok"))
    _print_json(data) if getattr(_args, "json", False) else None
    if not getattr(_args, "json", False):
        print(
            f"ok={ok} uptime_s={data.get('uptime_s')} "
            f"heartbeat_age_s={data.get('last_heartbeat_age_s')} "
            f"ollama={data.get('ollama')} vectors={data.get('index_vectors')} "
            f"nodes={data.get('graph_nodes')}"
        )
    return 0 if ok else 2


def cmd_sessions(args):
    data = _request("GET", "/sessions")
    sessions = data.get("sessions", [])
    if args.json:
        _print_json(sessions)
        return 0
    for s in sessions:
        print(
            f"{s.get('session_id')}  {s.get('title'):40.40}  "
            f"{s.get('preview', '')[:50]}"
        )
    return 0


def cmd_config(args):
    data = _request("GET", "/config/effective")
    if args.json:
        _print_json(data)
        return 0
    cfg = data.get("config", data) if isinstance(data, dict) else data
    if isinstance(cfg, list):
        for c in cfg:
            print(
                f"{c.get('key'):34} {str(c.get('value'))[:40]:40} "
                f"src={c.get('source')} secret={c.get('is_secret')}"
            )
    else:
        _print_json(data)
    return 0


def _roles_from_models_data(data):
    roles = data.get("roles", {})
    models = data.get("models", [])
    return roles, models


def cmd_models(args):
    data = _request("GET", "/llm/models/all")
    roles, models = _roles_from_models_data(data)
    if args.json:
        _print_json(data)
        return 0
    print("roles:")
    for role, mid in roles.items():
        print(f"  {role:8} -> {mid or '(unassigned)'}")
    print(f"models ({len(models)}):")
    for m in models:
        d = m if isinstance(m, dict) else {"id": str(m)}
        print(
            f"  {d.get('id')}  provider={d.get('provider')} "
            f"instruct={d.get('instruct')} "
            f"vision={d.get('vision')} free={d.get('free')}"
        )
    return 0


def cmd_roles(args):
    data = _request("GET", "/llm/models/all")
    roles, _models = _roles_from_models_data(data)
    if args.json:
        _print_json(roles)
        return 0
    for role, mid in roles.items():
        print(f"{role}: {mid or '(unassigned)'}")
    return 0


def cmd_tools(args):
    data = _request("GET", "/custom_tools")
    if args.json:
        _print_json(data)
        return 0
    tools = data.get("tools", data) if isinstance(data, dict) else data
    if isinstance(tools, list):
        for t in tools:
            d = t if isinstance(t, dict) else {"name": str(t)}
            # OpenAI-style: {"type":"function","function":{"name":...}}
            if "function" in d and isinstance(d["function"], dict):
                d = d["function"]
            name = d.get("name") or d.get("id") or "?"
            print(f"{name!s:32} {str(d.get('description', ''))[:70]}")
    else:
        print(json.dumps(data, indent=2))
    return 0


def cmd_tool_call(args):
    payload = {"name": args.name}
    if args.args:
        try:
            payload["args"] = json.loads(args.args)
        except json.JSONDecodeError:
            payload["args"] = args.args  # pass through as a string arg
    data = _request("POST", "/custom_tools/call", json_body=payload)
    if args.json:
        _print_json(data)
        return 0
    print(json.dumps(data, indent=2, default=str))
    return 0


def cmd_identity(args):
    data = _request("GET", "/identity")
    _print_json(data) if args.json else print(json.dumps(data, indent=2, default=str))
    return 0


def cmd_status(args):
    data = _request("GET", "/system/stats")
    _print_json(data) if args.json else print(json.dumps(data, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# Chat (WebSocket)
# ---------------------------------------------------------------------------


async def _ws_chat(ws, message: str, verbose: bool, show_stream: bool = True):
    """Send a chat message and drain events until the turn ends.

    Returns (exit_code, answer_text). answer_text is None if the turn failed.
    show_stream True prints live answer_chunk deltas (interactive UX); when
    False (--answer-only) stdout stays clean and only the final answer_done
    text is printed once.
    """
    await ws.send(json.dumps({"type": "chat", "message": message}))
    answer_parts: list[str] = []
    while True:
        raw = await ws.recv()
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            if verbose:
                print(f"[raw] {raw[:200]}")
            continue
        etype = ev.get("type", "")
        if verbose and etype in ("progress", "status", "heartbeat", "session_info"):
            print(f"[{etype}] {json.dumps(ev)[:160]}")
        if etype == "answer_chunk":
            content = ev.get("content", "")
            answer_parts.append(content)
            if show_stream:
                print(content, end="", flush=True)
        elif etype == "thinking":
            if verbose:
                print(f"[thinking] {ev.get('content', '')}")
        elif etype == "answer_done":
            # The authoritative final answer. answer_done content REPLACES the
            # streamed chunks (the UI swaps streamed prose for final text). In
            # live-stream mode we already printed chunks, so just close the
            # line; in --answer-only mode print the final exactly once.
            final = ev.get("content", "")
            if final:
                answer_parts = [final]
                if not show_stream:
                    print(final, end="", flush=True)
            print("")
            return 0, "".join(answer_parts)
        elif etype == "problem":
            diag = ev.get("diagnosis", {})
            problem = diag if isinstance(diag, dict) else {"detail": ev}
            print("")
            if verbose:
                print(f"[problem] {json.dumps(problem, indent=2)}")
            return 2, None
        elif etype in ("error", "stopped", "done"):
            print("")
            if verbose:
                print(f"[{etype}] {json.dumps(ev)[:200]}")
            return 2, None


def _chat(args) -> int:
    uri = _base().replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    if args.new:
        # /new is processed on the WS after connect; send it as a message first.
        pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run() -> int:
        async with websockets.connect(uri, open_timeout=10) as ws:
            if args.new:
                await ws.send(json.dumps({"type": "chat", "message": "/new"}))
                # drain a touch until we see the fresh session_info
                for _ in range(4):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except TimeoutError:
                        break
                    ev = json.loads(raw)
                    if ev.get("type") in ("progress", "status", "session_info"):
                        continue
                    break
            return await _ws_chat(
                ws, args.message, args.verbose, show_stream=not args.answer_only
            )

    try:
        code, answer = loop.run_until_complete(run())
    except (OSError, websockets.exceptions.WebSocketException) as e:
        print(f"ERROR: could not connect to {uri}: {e}", file=sys.stderr)
        return 1
    finally:
        loop.close()
    if args.json:
        print(json.dumps({"exit_code": code, "answer": answer}, indent=2))
    else:
        if answer is not None and args.answer_only:
            print(f"ANSWER: {answer}", file=sys.stderr)
    return code


def cmd_ws(args):
    """Raw WS mode: send a message, print EVERY event as JSON on its own line."""
    uri = _base().replace("http://", "ws://") + "/ws"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run():
        async with websockets.connect(uri, open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "chat", "message": args.message}))
            while True:
                raw = await ws.recv()
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    print(f'{{"type":"raw","content":{json.dumps(raw[:200])}}}')
                    continue
                print(json.dumps(ev))
                if ev.get("type") in ("answer_done", "problem", "error", "stopped"):
                    break

    try:
        loop.run_until_complete(run())
    except (OSError, websockets.exceptions.WebSocketException) as e:
        print(f"ERROR: could not connect to {uri}: {e}", file=sys.stderr)
        return 1
    finally:
        loop.close()
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        prog="vaultbot_cli", description="Scriptable client for the VaultBot backend."
    )
    p.add_argument("--base", help=f"base URL (default {DEFAULT_BASE})")
    p.add_argument("--token", help="auth token (default: read auth file)")
    sub = p.add_subparsers(dest="command", required=True)

    # health
    sp = sub.add_parser("health", help="GET /health")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_health)

    # chat
    sp = sub.add_parser("chat", help="send a chat message over WebSocket")
    sp.add_argument("message")
    sp.add_argument("--new", action="store_true", help="start a fresh session")
    sp.add_argument(
        "--verbose", "-v", action="store_true", help="show progress/status events"
    )
    sp.add_argument(
        "--json", action="store_true", help="print {exit_code, answer} as JSON"
    )
    sp.add_argument(
        "--answer-only",
        action="store_true",
        help="print body to stdout, ANSWER: <text> to stderr",
    )
    sp.set_defaults(func=_chat)

    # sessions
    sp = sub.add_parser("sessions", help="list sessions")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_sessions)

    # config
    sp = sub.add_parser("config", help="show effective config")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_config)

    # models / roles
    sp = sub.add_parser("models", help="show model pot + role assignment")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_models)
    sp = sub.add_parser("roles", help="show role -> model mapping")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_roles)

    # tools
    sp = sub.add_parser("tools", help="list custom tools")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_tools)
    sp = sub.add_parser("tool-call", help="call a custom tool")
    sp.add_argument("name")
    sp.add_argument("args", nargs="?", default="{}")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_tool_call)

    # identity / status
    sp = sub.add_parser("identity", help="show identity")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_identity)
    sp = sub.add_parser("status", help="system stats")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    # raw ws
    sp = sub.add_parser("ws", help="raw websocket chat stream (every event as JSON)")
    sp.add_argument("message")
    sp.set_defaults(func=cmd_ws)

    args = p.parse_args(argv)
    if getattr(args, "base", None):
        os.environ["VAULTBOT_BASE"] = args.base
    if getattr(args, "token", None):
        os.environ["VAULTBOT_TOKEN"] = args.token
    try:
        return args.func(args)
    except ApiError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
