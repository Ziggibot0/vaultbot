"""Tests for vault_graph concurrency — thread safety of the RLock guard.

The watchdog / autonomous researcher thread calls refresh() while the chat
loop reads nodes/edges/backlinks via dangling_links(), thin_notes(),
neighbors(), etc.  Without a lock this raises
``RuntimeError: dictionary changed size during iteration``.

These tests hammer refresh() in one thread while reading from another,
asserting no RuntimeError for thousands of iterations.
"""

from __future__ import annotations

import threading
import time

import pytest

pytestmark = pytest.mark.unit


from vault_graph import VaultGraph


def test_concurrent_refresh_and_read_no_runtime_error(tmp_path):
    """Spin a thread calling refresh() in a loop while the main thread
    reads dangling_links() + thin_notes() + neighbors().  Must not raise
    RuntimeError: dictionary changed size during iteration."""

    # Create a few notes so the graph has content to iterate over.
    for i in range(5):
        (tmp_path / f"Note-{i}.md").write_text(
            f"# Note {i}\n\nLinks to [[Note-{(i + 1) % 5}]] and [[Missing-Target]].\n",
            encoding="utf-8",
        )

    graph = VaultGraph(vault_path=str(tmp_path))

    errors: list[Exception] = []
    stop = threading.Event()

    def _hammer_refresh():
        """Continuously call refresh() — mutates nodes/edges."""
        while not stop.is_set():
            try:
                graph.refresh()
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                errors.append(e)
                return
            time.sleep(0.001)  # tiny pause to interleave with reads

    def _hammer_reads():
        """Continuously read from the graph — iterates nodes/edges."""
        while not stop.is_set():
            try:
                graph.dangling_links()
                graph.thin_notes()
                graph.neighbors("note-0")
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                errors.append(e)
                return
            time.sleep(0.001)

    # Start 1 writer + 2 readers.
    threads = [
        threading.Thread(target=_hammer_refresh, name="refresh"),
        threading.Thread(target=_hammer_reads, name="read-1"),
        threading.Thread(target=_hammer_reads, name="read-2"),
    ]
    for t in threads:
        t.start()

    # Let them run for 500ms — enough for thousands of interleaved ops.
    time.sleep(0.5)
    stop.set()

    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive(), f"thread {t.name} did not stop"

    assert not errors, f"concurrent access raised: {errors}"


def test_concurrent_refresh_and_walk_no_runtime_error(tmp_path):
    """Walk() iterates nodes + edges deeply — must be safe under concurrent
    refresh()."""
    for i in range(10):
        (tmp_path / f"Note-{i}.md").write_text(
            f"# Note {i}\n\nLinks to [[Note-{(i + 1) % 10}]] and [[Note-{(i + 2) % 10}]].\n",
            encoding="utf-8",
        )

    graph = VaultGraph(vault_path=str(tmp_path))

    errors: list[Exception] = []
    stop = threading.Event()

    def _hammer_refresh():
        while not stop.is_set():
            try:
                graph.refresh()
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                errors.append(e)
                return

    def _hammer_walk():
        while not stop.is_set():
            try:
                graph.walk(["Note-0", "Note-1"], depth=2)
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                errors.append(e)
                return

    threads = [
        threading.Thread(target=_hammer_refresh),
        threading.Thread(target=_hammer_walk),
    ]
    for t in threads:
        t.start()

    time.sleep(0.5)
    stop.set()

    for t in threads:
        t.join(timeout=5)

    assert not errors, f"concurrent walk raised: {errors}"
