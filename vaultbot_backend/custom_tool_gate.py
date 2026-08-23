"""Gate for agent-authored custom tools (issue #228).

WHY THIS EXISTS
---------------
``tool_create`` lets the agent write a new tool to ``custom_tools/`` and the
backend ``importlib``-loads it IN the live backend process. ``execute_custom_tool``
then runs it with full ``os`` / ``subprocess`` / network access and no scrubbed
env. A prompt-injected model could author a tool that reads ``.env`` /
``providers.json`` / ``google_workspace_tokens.json`` and exfiltrates them over
the network.

The curated ``custom_tools/`` fleet (gh_client, google_workspace, web_read_source,
ask_user, plugin_reload, ...) does exactly this *legitimately* — and some of it
needs live-process shared state (ask_user's pending-request queue, plugin_reload)
— so it CANNOT be isolated at load/execute time without breaking the fleet.

THE LINE: trusted vs untrusted
------------------------------
The curated tools are committed to the repo and trusted. A tool the agent
authores at RUNTIME is untrusted. So this gate runs at CREATION time
(``tool_create``), not at load/execute time. It blocks agent-authored tool code
from importing exfiltration/escape primitives (network, raw OS/process, dynamic
import) unless the author proves the intent with a ``doc_source`` — the same
friction + logging model as ``safe_write``'s doc-source gate and ``code_run``'s
``allow_write``. The curated fleet never passes through the gate, so it keeps
working unchanged.

Residual risk (documented, tracked): a model could still ``safe_write`` a file
directly into ``custom_tools/`` to bypass this gate. That path is a higher-effort
attack and is independently constrained by ``safe_write``'s own doc-source gate +
import-target check + pytest. A hard load-time allowlist was rejected because it
would break the curated fleet and require allowlisting every new trusted tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import safe_writer

# Top-level modules whose import enables exfiltration or sandbox escape. A tool
# that imports any of these is doing network I/O, raw filesystem/process access,
# or dynamic import — capabilities the curated (trusted, committed) tools already
# own. An agent-authored (untrusted) tool reaching for them is the #228 vector.
DANGEROUS_IMPORTS: frozenset[str] = frozenset(
    {
        # Network — exfiltrate secrets over the wire.
        "socket",
        "ssl",
        "http",
        "urllib",
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
        # Raw OS / process / filesystem — read .env, spawn processes.
        "os",
        "subprocess",
        "shutil",
        "pty",
        "ctypes",
        # Dynamic import / interpreter — load ANY module, bypassing the gate.
        "importlib",
        "builtins",
        "sys",
    }
)


def _internal_modules(backend_dir: Path) -> set[str]:
    """Build the 'internal' module set for detect_external_imports (mirrors
    safe_write): the backend's own .py stems plus its packages."""
    return {p.stem for p in backend_dir.glob("*.py")} | {
        "routers",
        "custom_tools",
        "identity",
    }


def gate_agent_tool_code(
    code: str, backend_dir: Path, doc_source: str | None = None
) -> dict[str, Any]:
    """Reject agent-authored tool code that imports dangerous modules without a
    ``doc_source``.

    Reuses ``safe_writer.detect_external_imports`` (the same detector safe_write
    uses for its doc-source gate) and intersects the result with
    ``DANGEROUS_IMPORTS``. Returns ``{"status": "ok" | "rejected", ...}``.

    A ``doc_source`` does not *approve* the import silently — it records intent
    so the operator can review; the caller is expected to log the approval.
    """
    external = safe_writer.detect_external_imports(code, _internal_modules(backend_dir))
    dangerous = [m for m in external if m in DANGEROUS_IMPORTS]
    if not dangerous:
        return {"status": "ok", "dangerous_imports": []}

    if not doc_source:
        return {
            "status": "rejected",
            "dangerous_imports": dangerous,
            "error": (
                f"Tool code imports {', '.join(dangerous)} — module(s) that "
                "enable network exfiltration or sandbox escape. Agent-authored "
                "tools must not reach for raw network, OS, or dynamic-import "
                "primitives."
            ),
            "hint": (
                "Use the sanctioned primitives instead (vault reads via the "
                "existing helpers, pure logic, safe_write for files). If the "
                "tool genuinely needs one of these, pass "
                "doc_source=<official docs URL> to record intent — it will be "
                "logged for review."
            ),
        }

    return {"status": "ok", "dangerous_imports": dangerous}
