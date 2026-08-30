from __future__ import annotations

import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import workspace as workspace_router
from workspace import WorkspaceRegistry

pytestmark = pytest.mark.unit


@pytest.fixture
def client(tmp_path, monkeypatch):
    registry = WorkspaceRegistry(tmp_path / "state.json")
    monkeypatch.setattr(workspace_router, "workspace_registry", registry)
    app = FastAPI()
    app.include_router(workspace_router.router)
    return TestClient(app)


def _repo(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/acme/widget.git"],
        cwd=root,
        check=True,
    )
    return root


def test_select_status_disconnect(client, tmp_path):
    root = _repo(tmp_path)

    selected = client.post("/workspace/select", json={"local_root": str(root)})
    assert selected.status_code == 200
    assert selected.json()["workspace"]["repository"] == "widget"
    assert client.get("/workspace").json()["status"] == "selected"

    disconnected = client.delete("/workspace")
    assert disconnected.status_code == 200
    assert client.get("/workspace").json()["status"] == "disconnected"


def test_select_rejects_non_repository(client, tmp_path):
    response = client.post("/workspace/select", json={"local_root": str(tmp_path)})
    assert response.status_code == 400
    assert response.json()["detail"] == "Could not select that Git repository."


def test_repository_listing_is_secret_free(client, monkeypatch):
    monkeypatch.setattr(workspace_router, "gh_available", lambda: True)
    monkeypatch.setattr(
        workspace_router,
        "gh_api",
        lambda *args, **kwargs: [
            {
                "full_name": "acme/widget",
                "private": True,
                "default_branch": "trunk",
                "clone_url": "https://github.com/acme/widget.git",
                "permissions": {"admin": True},
            }
        ],
    )

    payload = client.get("/workspace/repositories").json()

    assert payload == {
        "repositories": [
            {
                "full_name": "acme/widget",
                "private": True,
                "default_branch": "trunk",
                "clone_url": "https://github.com/acme/widget.git",
            }
        ]
    }


def test_clone_rejects_invalid_repository_name(client, monkeypatch):
    monkeypatch.setattr(workspace_router, "gh_available", lambda: True)
    response = client.post("/workspace/clone", json={"full_name": "../escape"})
    assert response.status_code == 400
