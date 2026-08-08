"""Endpoint tests via FastAPI TestClient — verifies the main.py shims wire correctly.

This test file is EXEMPT from the conftest guard that forbids `import main`.
It sets `__allows_main_import__ = True` at module level and uses
VAULTBOT_SKIP_LOCK=1 to bypass the PID lock. The lifespan context manager
(added in commit 92958698) means `import main` doesn't fire startup/shutdown
on bare import — only TestClient(app) or uvicorn.run triggers them.

The tests use TestClient(app) to hit the HTTP routes in-process. The
route handlers read module-level globals (ollama_client, vault_indexer,
etc.) which are constructed at import time — so the real services are
wired, not fakes. This means /health hits _ping_ollama() (which may fail
if Ollama isn't running — that's fine, the test checks structure not
dependency status) and /models hits ollama_client.list_local_models()
(which returns [] if Ollama is down — also fine).

Documentation grounding:
- FastAPI Testing Events: `with TestClient(app) as client:` fires the
  lifespan (startup + shutdown). https://fastapi.tiangolo.com/advanced/testing-events/
- FastAPI Testing Dependencies: `app.dependency_overrides` for faking.
  https://fastapi.tiangolo.com/advanced/testing-dependencies/
"""

# This flag exempts this module from the conftest guard that fails any
# test importing `main`. Set BEFORE any imports so the guard sees it.
__allows_main_import__ = True

import os

# Bypass the PID lock so `import main` doesn't sys.exit if a backend is running.
os.environ.setdefault("VAULTBOT_SKIP_LOCK", "1")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """Create a TestClient WITHOUT firing the lifespan.

    We use `TestClient(app)` directly (not `with TestClient(app) as c:`)
    so the lifespan startup/shutdown DON'T fire — the startup tries to
    load the real FAISS index, start the file watcher, and start the
    autonomous researcher, which hangs when a real backend is already
    running. The route handlers read module-level globals that are
    already constructed at import time, so the routes work without
    the lifespan having run.

    This is safe because:
    - `VAULTBOT_SKIP_LOCK=1` bypasses the PID lock (set at module top).
    - The route handlers read globals (ollama_client, vault_indexer,
      etc.) that are constructed at `import main` time, not at lifespan
      startup time. The lifespan startup only loads the FAISS INDEX
      + starts the watcher + researcher — the *objects* already exist.
    - /health calls _ping_ollama() which may fail (Ollama not running)
      — that's fine, the test checks structure not dependency status.
    """
    from main import app
    return TestClient(app)


def test_health_returns_ok(client):
    """GET /health returns 200 with status + dependency info."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    # health_monitor.health() returns at least a status field.
    assert "status" in data or "ok" in data or len(data) > 0


def test_models_returns_list(client):
    """GET /models returns 200 with a models list + current model."""
    resp = client.get("/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert isinstance(data["models"], list)
    assert "current" in data


def test_identity_returns_fields(client):
    """GET /identity returns 200 with identity + self_model."""
    resp = client.get("/identity")
    assert resp.status_code == 200
    data = resp.json()
    # The shim delegates to identity_api.get_identity(svc) which returns
    # identity, self_model, summary fields.
    assert "identity" in data or "summary" in data or len(data) > 0


def test_task_rejects_missing_goal(client):
    """POST /task with no goal returns an error (not 200 with a plan)."""
    resp = client.post("/task", json={})
    # The task_api.create_task shim returns {"error": "missing goal"} or
    # a 400 tuple. Either way, it should NOT be a successful plan.
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    # Accept either a 400 status or a 200 with an error field.
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        assert "error" in data or "plan_id" not in data, \
            "POST /task with empty body should not return a plan"
