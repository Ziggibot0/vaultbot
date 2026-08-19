"""
Agent-authored tool: backend_restart
"""

SCHEMA = {
    "name": "backend_restart",
    "description": "Restart the VaultBot backend process and reconnect. Sends a WebSocket message to the Obsidian plugin, which calls restartBackend() — the exact same code path as the GUI restart button. The plugin handles shutdown + respawn. Before restarting, this tool automatically caches recent chat history to RESTART_CONTEXT.md so the next session boots with full context — no manual resume needed. Use this after self-edits that require a backend restart, or to recover from a stale state.",
    "parameters": {"properties": {}, "required": [], "type": "object"},
}


def run(args: dict) -> dict:
    """Restart the VaultBot backend by asking the Obsidian plugin to do it.

    Before sending the restart signal, this tool:
    1. Reads the CURRENT session's working memory plan and conversation
       history from session_state/ (NOT old chat notes from 08-Chat/).
    2. Writes them to vaultbot_backend/identity/RESTART_CONTEXT.md
    3. The Identity layer picks this up on boot and injects it into the
       system prompt automatically, then deletes the file (one-shot).

    This means after a restart, the agent wakes up with the EXACT plan it
    was working on (with step statuses) and the most recent conversation
    turns — not old chat notes from days ago.

    The plugin then:
    1. Calls POST /shutdown (graceful backend shutdown)
    2. Waits 1 second for port release
    3. Spawns a fresh backend process via startBackendIfNeeded()
    """
    import glob
    import json
    import os
    import time
    import urllib.request

    # --- Cache context before restart ---------------------------------
    try:
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        identity_dir = os.path.join(backend_dir, "identity")
        session_state_dir = os.path.join(backend_dir, "session_state")
        restart_ctx_path = os.path.join(identity_dir, "RESTART_CONTEXT.md")

        parts = []
        parts.append("# RESTART CONTEXT")
        parts.append(
            f"Restart triggered at: "
            f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )
        parts.append("")
        parts.append(
            "You were restarted mid-session. Below is your CURRENT plan "
            "(with step statuses) and recent conversation. Continue from "
            "where you left off — do NOT ask the operator to re-explain. "
            "Do NOT start a new task. Pick up the EXACT plan below."
        )
        parts.append("")

        # ── Read the CURRENT session's working memory plan ──────────
        # Use the last-active-session pointer (deterministic) instead of
        # the old "most-recent file by mtime" heuristic, which with 60+
        # accumulated files frequently picked the WRONG session. Fall back
        # to mtime only if the pointer is absent.
        try:
            from last_session import read as _read_last_session

            _pointer_sid = _read_last_session()
        except Exception:
            _pointer_sid = None
        wm_files = []
        if os.path.isdir(session_state_dir):
            # Prefer the pointer session's working memory file.
            if _pointer_sid:
                _p = os.path.join(
                    session_state_dir, f"working_memory_state_{_pointer_sid}.json"
                )
                if os.path.exists(_p):
                    wm_files.append((os.path.getmtime(_p), _p))
            # Fall back: most recent by mtime (covers pre-pointer state).
            if not wm_files:
                for f in glob.glob(
                    os.path.join(session_state_dir, "working_memory_state_*.json")
                ):
                    mtime = os.path.getmtime(f)
                    wm_files.append((mtime, f))
                wm_files.sort(key=lambda x: x[0], reverse=True)

        plan_included = False
        if wm_files:
            try:
                with open(wm_files[0][1], encoding="utf-8") as f:
                    wm_data = json.load(f)
                if isinstance(wm_data, dict) and wm_data.get("tasks"):
                    parts.append("## YOUR CURRENT PLAN (with step statuses)")
                    parts.append(f"Goal: {wm_data.get('goal', '(no goal)')}")
                    parts.append("")
                    for t in wm_data.get("tasks", []):
                        mark = {
                            "completed": "[x]",
                            "in_progress": "[~]",
                            "pending": "[ ]",
                        }.get(t.get("status", ""), "[ ]")
                        parts.append(
                            f"{mark} {t.get('id', '?')}. {t.get('content', '')}"
                        )
                        if t.get("notes"):
                            parts.append(f"   Notes: {t['notes']}")
                    done = sum(
                        1
                        for t in wm_data.get("tasks", [])
                        if t.get("status") == "completed"
                    )
                    total = len(wm_data.get("tasks", []))
                    parts.append(f"Progress: {done}/{total} done")
                    # Include step summaries for completed steps.
                    summaries = wm_data.get("step_summaries", {})
                    if summaries:
                        parts.append("")
                        parts.append("Step summaries (what was accomplished):")
                        for tid, summary in summaries.items():
                            parts.append(f"  Step {tid}: {summary[:500]}")
                    parts.append("")
                    plan_included = True
            except Exception as e:  # noqa: BLE001 — best-effort
                parts.append(f"(Could not read working memory: {e})")
                parts.append("")

        if not plan_included:
            parts.append("## No active plan found")
            parts.append("(No working memory state file existed at restart time.)")
            parts.append("")

        # ── Read the CURRENT session's conversation history ─────────
        # Same pointer-first approach as the working memory lookup above.
        conv_files = []
        if os.path.isdir(session_state_dir):
            if _pointer_sid:
                _p = os.path.join(
                    session_state_dir, f"conversation_state_{_pointer_sid}.json"
                )
                if os.path.exists(_p):
                    conv_files.append((os.path.getmtime(_p), _p))
            if not conv_files:
                for f in glob.glob(
                    os.path.join(session_state_dir, "conversation_state_*.json")
                ):
                    mtime = os.path.getmtime(f)
                    conv_files.append((mtime, f))
                conv_files.sort(key=lambda x: x[0], reverse=True)

        if conv_files:
            try:
                with open(conv_files[0][1], encoding="utf-8") as f:
                    conv_data = json.load(f)
                if isinstance(conv_data, list) and conv_data:
                    parts.append("## Recent Conversation (last few turns)")
                    # Only include the last 8 messages (4 turns) to keep
                    # the restart context focused and bounded.
                    recent = conv_data[-8:]
                    for msg in recent:
                        role = msg.get("role", "?")
                        content = msg.get("content", "") or ""
                        # Skip system messages (they're rebuilt on boot).
                        if role == "system":
                            continue
                        # Skip tool results (they're huge and the model
                        # already processed them).
                        if role == "tool":
                            tool_name = msg.get("tool_name", "tool")
                            parts.append(
                                f"[{role}] Called {tool_name} — result omitted "
                                f"(re-call the tool if needed)"
                            )
                            continue
                        # Truncate long messages.
                        if len(content) > 500:
                            content = content[:500] + "...[truncated]"
                        parts.append(f"[{role}] {content}")
                    parts.append("")
            except Exception as e:  # noqa: BLE001 — best-effort
                parts.append(f"(Could not read conversation history: {e})")
                parts.append("")

        restart_context = "\n".join(parts)

        os.makedirs(identity_dir, exist_ok=True)
        with open(restart_ctx_path, "w", encoding="utf-8") as f:
            f.write(restart_context)

        cached = True
        cache_msg = (
            f"Cached current plan ({'found' if plan_included else 'none'}) "
            f"and conversation to RESTART_CONTEXT.md"
        )
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        cached = False
        cache_msg = f"Failed to cache context: {e}"

    # --- Send restart signal ------------------------------------------
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/restart",
            method="POST",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        return {
            "status": "restart_requested",
            "message": "Restart scheduled. Backend will restart in ~3 seconds — the chat loop will finish processing this tool result first, then the plugin will kill and respawn the backend. MCP client reconnects automatically.",
            "context_cached": cached,
            "cache_message": cache_msg,
            "response": result,
        }
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        return {
            "status": "error",
            "message": f"Could not reach /restart endpoint: {e}. Make sure the backend is running and the plugin is connected.",
            "context_cached": cached,
            "cache_message": cache_msg,
            "error": str(e),
        }
