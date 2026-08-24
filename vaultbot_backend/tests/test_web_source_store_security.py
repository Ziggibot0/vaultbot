"""Security tests for archived web source path handling."""

from __future__ import annotations

import pytest

import web_source_store

pytestmark = pytest.mark.unit


def test_source_path_accepts_valid_html_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(web_source_store, "WEB_DIR", tmp_path)
    got = web_source_store.source_path("example-com-1a2b3c4d.html")
    assert got == tmp_path / "example-com-1a2b3c4d.html"


@pytest.mark.parametrize(
    "filename",
    [
        "../secrets.env",
        "../../vaultbot_backend/.vaultbot_auth_token",
        "/etc/passwd",
        "nested/path.html",
        "not-html.txt",
        "",
    ],
)
def test_source_path_rejects_traversal_and_non_html(monkeypatch, tmp_path, filename):
    monkeypatch.setattr(web_source_store, "WEB_DIR", tmp_path)
    with pytest.raises(ValueError):
        web_source_store.source_path(filename)


def test_web_read_source_rejects_invalid_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(web_source_store, "WEB_DIR", tmp_path)
    from custom_tools.web_read_source import run

    got = run({"file": "../.env"})
    assert "invalid archived filename" in (got.get("error") or "")
