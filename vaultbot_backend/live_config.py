"""live_config.py — runtime-mutable config for safe mode + contributions.

WHY THIS EXISTS
---------------
Safe Mode and the contributions opt-in are read from process environment
variables (VAULTBOT_SAFE_MODE, VAULTBOT_ALLOW_CONTRIBUTIONS) that the plugin
sets ONCE when it spawns the backend. That means toggling either in the
settings GUI only takes effect after a backend restart — the running backend
keeps the spawn-time value, so the GUI and the backend disagree (split-brain
config, issue #257).

This module is the single live source of truth for both settings. It is
seeded from the environment at startup (so a fresh spawn honors .env / the
plugin's spawn env), then updated at runtime via POST /config. Every reader
(safe_mode.py, the contribution tools, self_improver.py) goes through this
module, so a GUI toggle takes effect immediately without a restart.

Thread-safe: a module-level lock guards the mutable state.
"""

from __future__ import annotations

import os
import threading

_lock = threading.Lock()
# None = not yet overridden at runtime → fall back to the environment.
_safe_mode: bool | None = None
_allow_contributions: bool | None = None


def _env_safe_mode() -> bool:
    """Safe Mode is ON by default; only explicit disable turns it off."""
    val = os.environ.get("VAULTBOT_SAFE_MODE", "true").strip().lower()
    return val not in ("0", "false", "off", "no", "developer")


def _env_allow_contributions() -> bool:
    """Contributions are OFF by default; only explicit 'true' opts in."""
    return os.environ.get("VAULTBOT_ALLOW_CONTRIBUTIONS", "").strip().lower() == "true"


def is_safe_mode() -> bool:
    """Return the current Safe Mode state (runtime override, else env)."""
    with _lock:
        if _safe_mode is not None:
            return _safe_mode
    return _env_safe_mode()


def allow_contributions() -> bool:
    """Return the current contributions opt-in state (runtime, else env)."""
    with _lock:
        if _allow_contributions is not None:
            return _allow_contributions
    return _env_allow_contributions()


def set_safe_mode(value: bool | None) -> None:
    """Override Safe Mode at runtime (called by POST /config).

    Pass None to clear the override and fall back to the environment.
    """
    global _safe_mode
    with _lock:
        _safe_mode = None if value is None else bool(value)


def set_allow_contributions(value: bool | None) -> None:
    """Override the contributions opt-in at runtime (called by POST /config).

    Pass None to clear the override and fall back to the environment.
    """
    global _allow_contributions
    with _lock:
        _allow_contributions = None if value is None else bool(value)
