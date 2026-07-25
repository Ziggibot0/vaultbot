"""
VaultBot supervision layer: liveness/health/heartbeat monitoring + Windows service install script.

nssm (Non-Sucking Service Manager) is the Windows equivalent of systemd. It wraps a
regular console process into a proper Windows service with boot-start, crash-restart,
log rotation, and graceful shutdown semantics.

This module provides:
  - `HealthMonitor`: a liveness monitor that tracks heartbeats from the autonomous
    researcher loop and exposes a health snapshot. A background watchdog thread
    detects hangs (heartbeat stale > 1 hour) without killing the process — the
    caller decides what to do on staleness.
  - `generate_nssm_install` / `generate_nssm_uninstall`: emit nssm commands so VaultBot
    can be installed as a Windows service that:
      * starts on boot (SERVICE_DELAYED_AUTO_START),
      * restarts on crash (AppExit Default Restart, 5s delay),
      * rotates logs (AppRotateFiles / AppRotateBytes at 10MB),
      * shuts down gracefully (AppStopMethodConsole 60s).

Together this lets VaultBot run for days unattended: the backend self-reports
heartbeats, the watchdog flags hangs, and nssm keeps the process alive across
crashes and reboots.

Pure stdlib only (threading, time, os, pathlib, typing). No external dependencies.
No imports of other VaultBot modules at module level — refs are passed in via the
constructor to avoid circular imports.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional


# Heartbeat is considered stale after this many seconds (1 hour = probably hung).
HEARTBEAT_STALE_SECONDS = 3600


class HealthMonitor:
    """Liveness/health/heartbeat monitor for the VaultBot backend.

    The autonomous researcher calls `heartbeat(task)` each loop iteration. The
    monitor tracks the last heartbeat time and current task, exposes a health
    snapshot via `health()`, and optionally runs a passive background watchdog
    that flags staleness without killing the process.
    """

    def __init__(self, session_logger: Optional[object] = None) -> None:
        # session_logger is optional; if provided it should have a .log(msg) or
        # similar method. We call it defensively inside try/except so a bad
        # logger never crashes the monitor or the main process.
        self._session_logger = session_logger

        self._start_time: float = time.time()
        self._last_heartbeat: float = time.time()
        self._current_task: str = ""

        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Heartbeat / health snapshot
    # ------------------------------------------------------------------
    def heartbeat(self, task: str = "") -> None:
        """Record a heartbeat. Called each loop iteration by the researcher.

        Updates the last-heartbeat timestamp and the current task description.
        Never raises.
        """
        try:
            self._last_heartbeat = time.time()
            self._current_task = task
        except Exception:
            # Heartbeat must never crash the caller.
            pass

    def health(self, extra: Optional[dict] = None) -> dict:
        """Return a health snapshot dict.

        Fields:
          ok                : bool, False if heartbeat is stale (> 1 hour).
          uptime_s          : int, seconds since monitor start.
          last_heartbeat_age_s : int, seconds since last heartbeat.
          current_task      : str, last reported task.
          timestamp         : float, current epoch time.

        Merges `extra` (non-None) into the snapshot. Never raises.
        """
        try:
            now = time.time()
            uptime_s = int(now - self._start_time)
            last_heartbeat_age_s = int(now - self._last_heartbeat)
            ok = last_heartbeat_age_s <= HEARTBEAT_STALE_SECONDS

            snapshot = {
                "ok": ok,
                "uptime_s": uptime_s,
                "last_heartbeat_age_s": last_heartbeat_age_s,
                "current_task": self._current_task,
                "timestamp": now,
            }
            if extra:
                try:
                    snapshot.update(extra)
                except Exception:
                    pass
            return snapshot
        except Exception:
            # Never let health() crash a caller (e.g. an HTTP handler).
            return {
                "ok": False,
                "uptime_s": 0,
                "last_heartbeat_age_s": 0,
                "current_task": "",
                "timestamp": time.time(),
            }

    def is_alive(self) -> bool:
        """True if the heartbeat is fresh (< HEARTBEAT_STALE_SECONDS old)."""
        try:
            age = time.time() - self._last_heartbeat
            return age < HEARTBEAT_STALE_SECONDS
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Watchdog thread
    # ------------------------------------------------------------------
    def start_watchdog(
        self,
        check_interval: int = 300,
        on_stale: Optional[Callable[[], None]] = None,
    ) -> None:
        """Start a passive background watchdog thread.

        Every `check_interval` seconds it checks `is_alive()`. If stale, it calls
        `on_stale()` (if provided) and logs. The watchdog is passive: it detects
        hangs but does NOT kill the process — the caller decides the response.

        Safe to call multiple times; re-arming a stopped watchdog reuses the same
        stop event (which is re-cleared). Never raises.
        """
        try:
            if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
                # Already running.
                return

            self._stop_event.clear()

            def _watch() -> None:
                try:
                    while not self._stop_event.is_set():
                        # Wait for the check interval, but wake early on stop.
                        if self._stop_event.wait(check_interval):
                            break
                        try:
                            if not self.is_alive():
                                self._log(
                                    "watchdog: heartbeat stale "
                                    f"(last_heartbeat_age_s="
                                    f"{int(time.time() - self._last_heartbeat)}s) — "
                                    "backend appears hung"
                                )
                                if on_stale is not None:
                                    try:
                                        on_stale()
                                    except Exception as exc:
                                        self._log(
                                            f"watchdog: on_stale callback raised: {exc!r}"
                                        )
                        except Exception as exc:
                            self._log(f"watchdog: check raised: {exc!r}")
                except Exception as exc:
                    self._log(f"watchdog: thread loop crashed: {exc!r}")

            thread = threading.Thread(
                target=_watch,
                name="vaultbot-health-watchdog",
                daemon=True,
            )
            self._heartbeat_thread = thread
            thread.start()
        except Exception as exc:
            self._log(f"start_watchdog failed: {exc!r}")

    def stop_watchdog(self) -> None:
        """Stop the watchdog thread (if running). Never raises."""
        try:
            self._stop_event.set()
            thread = self._heartbeat_thread
            if thread is not None and thread.is_alive():
                # Don't join forever — the daemon should exit promptly on stop.
                thread.join(timeout=5)
            self._heartbeat_thread = None
        except Exception as exc:
            self._log(f"stop_watchdog failed: {exc!r}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        """Best-effort logging via the injected session logger, if any."""
        try:
            logger = self._session_logger
            if logger is None:
                return
            # Support common logger shapes: .log(msg), .info(msg), plain callable.
            for attr in ("log", "info", "debug"):
                fn = getattr(logger, attr, None)
                if callable(fn):
                    fn(msg)
                    return
            if callable(logger):
                logger(msg)
        except Exception:
            # Logging must never crash the monitor.
            pass


# ----------------------------------------------------------------------
# nssm install / uninstall script generation
# ----------------------------------------------------------------------
def generate_nssm_install(
    vaultbot_dir: str,
    python_exe: str,
    log_dir: Optional[str] = None,
) -> str:
    """Return a string of `nssm` commands to install VaultBot as a Windows service.

    nssm is the Windows systemd equivalent. The generated commands configure:
      - boot start (SERVICE_DELAYED_AUTO_START, so it starts shortly after boot),
      - crash restart (AppExit Default Restart, 5s restart delay + throttle),
      - log rotation (stdout/stderr to files, rotate at 10MB),
      - graceful shutdown (AppStopMethodConsole 60s, so the backend gets up to
        a minute to finish in-flight work before being force-killed),
      - no console window (AppNoConsole 1, runs headless).

    Args:
        vaultbot_dir: absolute path to the vaultbot_backend directory (where
            main.py lives). Used as AppDirectory and to locate main.py.
        python_exe: absolute path to the Python interpreter to run main.py with.
        log_dir: directory for rotated log files. Defaults to `vaultbot_dir\\logs`.

    Returns:
        A multi-line string of nssm commands ready to paste into an elevated
        PowerShell/CMD window.
    """
    vdir = str(Path(vaultbot_dir))
    main_py = os.path.join(vdir, "main.py")

    if log_dir is None:
        log_dir = os.path.join(vdir, "logs")
    log_dir = str(Path(log_dir))

    stdout_log = os.path.join(log_dir, "vaultbot.out.log")
    stderr_log = os.path.join(log_dir, "vaultbot.err.log")

    header = (
        "# ----------------------------------------------------------------------\n"
        "# VaultBot Windows service install (via nssm)\n"
        "# ----------------------------------------------------------------------\n"
        "# nssm is the Windows equivalent of systemd. These commands register\n"
        "# VaultBot as a managed Windows service that:\n"
        "#   * starts on boot            (SERVICE_DELAYED_AUTO_START)\n"
        "#   * restarts on crash         (AppExit Default Restart, 5s delay/throttle)\n"
        "#   * rotates logs              (AppRotateFiles, 10MB per file)\n"
        "#   * shuts down gracefully     (AppStopMethodConsole 60s)\n"
        "# Run from an elevated (Administrator) PowerShell or CMD window.\n"
        "# Make sure nssm.exe is on PATH (choco install nssm, or download from\n"
        "# https://nssm.cc/).\n"
        "# ----------------------------------------------------------------------\n"
    )

    lines = [
        header,
        f'nssm install VaultBot "{python_exe}" "{main_py}"',
        f'nssm set VaultBot AppDirectory "{vdir}"',
        "nssm set VaultBot Start SERVICE_DELAYED_AUTO_START",
        "nssm set VaultBot AppExit Default Restart",
        "nssm set VaultBot AppRestartDelay 5000",
        "nssm set VaultBot AppThrottle 5000",
        "",
        "# Log rotation: redirect stdout/stderr to files and rotate at 10MB.",
        f'nssm set VaultBot AppStdout "{stdout_log}"',
        f'nssm set VaultBot AppStderr "{stderr_log}"',
        "nssm set VaultBot AppRotateFiles 1",
        "nssm set VaultBot AppRotateBytes 10485760",
        "nssm set VaultBot AppRotateOnline 1",
        "",
        "# Graceful shutdown: give the backend up to 60s to finish in-flight work",
        "# before nssm force-terminates the process tree.",
        "nssm set VaultBot AppStopMethodConsole 60000",
        "",
        "# Run headless (no console window).",
        "nssm set VaultBot AppNoConsole 1",
        "",
        "# Start the service now.",
        "nssm start VaultBot",
    ]
    return "\n".join(lines)


def generate_nssm_uninstall() -> str:
    """Return nssm commands to stop and remove the VaultBot service.

    Run from an elevated (Administrator) PowerShell/CMD window.
    """
    return (
        "# Stop and remove the VaultBot Windows service.\n"
        "# Run from an elevated (Administrator) PowerShell/CMD window.\n"
        "nssm stop VaultBot\n"
        "nssm remove VaultBot confirm"
    )


__all__ = [
    "HealthMonitor",
    "generate_nssm_install",
    "generate_nssm_uninstall",
    "HEARTBEAT_STALE_SECONDS",
]