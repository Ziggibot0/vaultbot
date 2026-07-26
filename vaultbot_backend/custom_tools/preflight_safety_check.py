"""
Agent-authored tool: preflight_safety_check
"""

SCHEMA = {"name": "preflight_safety_check", "description": "Pre-flight safety check before self-modifying operations. Verifies git clean state (for rollback safety), critical backend files exist, identity files intact, disk space adequate, custom tools still import cleanly, and vault directory is accessible. Returns PASS / WARN / BLOCK with full details. Run this before any code_write or tool_create operation to verify the system is healthy enough to safely edit.", "parameters": {"description": "No arguments needed. The tool auto-detects paths from its own file location.", "properties": {}, "type": "object"}}

import importlib.util
import shutil
import subprocess
import time
from pathlib import Path

# Determine backend directory from this file's location
# (custom_tools/preflight_safety_check.py -> parent.parent = vaultbot_backend/)
try:
    BACKEND_DIR = Path(__file__).resolve().parent.parent
except NameError:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
    git_root = Path(r.stdout.strip()) if r.returncode == 0 else Path(".").resolve()
    BACKEND_DIR = git_root / "vaultbot_backend"


def run(args):
    """
    Pre-flight safety check before self-modifying operations.
    
    Verifies:
    1. Git has a HEAD (so rollback is possible if a self-edit breaks something)
    2. All critical backend files are present
    3. Identity files (IDENTITY.md, SELF_MODEL.md, GOALS.md) are intact
    4. Disk space is adequate (>5% free)
    5. All existing custom tools still import cleanly
    6. Vault notes directory is accessible
    
    Returns:
        status: "PASS" (all clear), "WARN" (proceed with caution), or "BLOCK" (do not proceed)
        checks: detailed results for each check
        warnings: non-blocking issues
        blocks: blocking issues that make self-edit unsafe
    """
    results = {
        "status": "PASS",
        "checks": {},
        "warnings": [],
        "blocks": [],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

    backend_dir = BACKEND_DIR

    # --- 1. Git state check ---
    try:
        def git(*cmd):
            r = subprocess.run(["git"] + list(cmd), capture_output=True, text=True,
                             cwd=str(backend_dir), timeout=10)
            return r.stdout.strip(), r.stderr.strip(), r.returncode

        status_out, _, _ = git("status", "--porcelain")
        uncommitted = [l for l in status_out.splitlines() if l.strip()]
        head_out, _, head_rc = git("rev-parse", "HEAD")
        has_head = head_rc == 0

        git_check = {
            "clean_working_tree": len(uncommitted) == 0,
            "has_head_for_rollback": has_head,
            "uncommitted_files": uncommitted[:10],
            "uncommitted_count": len(uncommitted)
        }
        results["checks"]["git"] = git_check

        if not has_head:
            results["blocks"].append("No git HEAD — cannot roll back if self-edit fails")
            results["status"] = "BLOCK"
        elif len(uncommitted) > 0:
            results["warnings"].append(
                f"Working tree has {len(uncommitted)} uncommitted change(s) — "
                "rollback will revert these too"
            )
            if results["status"] == "PASS":
                results["status"] = "WARN"
    except Exception as e:
        results["warnings"].append(f"Git check failed: {e}")
        results["checks"]["git"] = {"error": str(e)}

    # --- 2. Critical backend files ---
    critical_files = [
        "main.py", "agent_tools.py", "self_improver.py",
        "vault_indexer.py", "note_creator.py", "research_engine.py",
        "identity.py", "fused_retrieval.py", "autonomous_researcher.py",
        "session_logger.py", "knowledge_curriculum.py",
    ]
    missing = [f for f in critical_files if not (backend_dir / f).exists()]

    results["checks"]["critical_files"] = {
        "checked_count": len(critical_files),
        "missing": missing,
        "all_present": len(missing) == 0
    }
    if missing:
        results["blocks"].append(f"Critical files missing: {missing}")
        results["status"] = "BLOCK"

    # --- 3. Identity files ---
    identity_dir = backend_dir / "identity"
    identity_files = ["IDENTITY.md", "SELF_MODEL.md", "GOALS.md"]
    identity_missing = [f for f in identity_files if not (identity_dir / f).exists()]

    results["checks"]["identity"] = {
        "dir_exists": identity_dir.exists(),
        "missing": identity_missing,
        "all_present": len(identity_missing) == 0
    }
    if identity_missing:
        results["blocks"].append(f"Identity files missing: {identity_missing}")
        results["status"] = "BLOCK"

    # --- 4. Disk space ---
    try:
        usage = shutil.disk_usage(str(backend_dir))
        disk_pct = (usage.used / usage.total) * 100
        results["checks"]["disk"] = {
            "total_gb": round(usage.total / (1024**3), 1),
            "used_gb": round(usage.used / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "used_pct": round(disk_pct, 1)
        }
        if disk_pct > 95:
            results["blocks"].append(f"Disk nearly full: {disk_pct:.1f}%")
            results["status"] = "BLOCK"
        elif disk_pct > 90:
            results["warnings"].append(f"Disk getting full: {disk_pct:.1f}%")
            if results["status"] == "PASS":
                results["status"] = "WARN"
    except Exception as e:
        results["warnings"].append(f"Disk check failed: {e}")

    # --- 5. Custom tools integrity ---
    custom_dir = backend_dir / "custom_tools"
    tool_files = []
    broken_tools = []
    if custom_dir.exists():
        tool_files = [f for f in custom_dir.glob("*.py")
                     if f.name != "__init__.py" and f.name != "preflight_safety_check.py"]

    for tf in tool_files:
        try:
            mod_name = f"custom_tools.{tf.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, tf)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if not hasattr(mod, "run"):
                    broken_tools.append({"file": tf.name, "error": "No run() function"})
        except Exception as e:
            broken_tools.append({"file": tf.name, "error": str(e)})

    results["checks"]["custom_tools"] = {
        "total": len(tool_files),
        "broken": broken_tools,
        "all_healthy": len(broken_tools) == 0
    }
    if broken_tools:
        results["warnings"].append(
            f"Broken custom tools: {[b['file'] for b in broken_tools]}"
        )
        if results["status"] == "PASS":
            results["status"] = "WARN"

    # --- 6. Vault notes directory ---
    vault_dir = backend_dir.parent / "vaultbot"
    results["checks"]["vault"] = {
        "exists": vault_dir.exists(),
        "note_count": len(list(vault_dir.rglob("*.md"))) if vault_dir.exists() else 0
    }
    if not vault_dir.exists():
        results["blocks"].append("Vault notes directory not found")
        results["status"] = "BLOCK"

    # --- Final status ---
    if results["blocks"]:
        results["status"] = "BLOCK"
    elif results["warnings"] and results["status"] == "PASS":
        results["status"] = "WARN"

    return results
