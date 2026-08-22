"""Integration test for the full Prove-Code-Change flow (issue #207, Gap 3).

The prove-then-write contract is: an edit that imports a non-VaultBot
module must carry a ``doc_source`` (official-docs URL) or ``safe_write``
rejects it. ``Check-API-Against-Docs`` produces that source by mapping
each external import to its docs domain (now via ``doc_domains``) and
digging only authoritative sources.

This test ties the three pieces together end-to-end at the enforcement
boundary (the only part that runs without a live backend / Ollama):

  1. ``detect_external_imports`` finds the external modules.
  2. ``resolve_doc_domain`` maps each to its canonical docs domain.
  3. ``safe_write`` accepts the edit when the derived ``doc_source`` is
     attached, and rejects it when it is not.

It also asserts the procedure note on disk actually delegates to
``doc_domains`` (the map is externalized, not still hardcoded).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

import safe_writer
import self_improver
from doc_domains import resolve_doc_domain

# The real procedure note, read from disk (never written).
_PROCEDURES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "vault"
    / "vaultbot-stuff"
    / "System"
    / "Procedures"
)


@pytest.fixture
def patched_improver(tmp_path, monkeypatch):
    """A SelfImprover whose BACKEND_DIR / BACKEND_ROOT / CUSTOM_TOOLS_DIR
    all point at a throwaway tmp tree (mirrors test_safe_write.py)."""
    backend_dir = tmp_path / "vaultbot_backend"
    backend_dir.mkdir()
    custom_tools = backend_dir / "custom_tools"
    custom_tools.mkdir()
    (custom_tools / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(self_improver, "BACKEND_ROOT", tmp_path, raising=True)
    monkeypatch.setattr(self_improver, "BACKEND_DIR", backend_dir, raising=True)
    monkeypatch.setattr(self_improver, "CUSTOM_TOOLS_DIR", custom_tools, raising=True)
    return self_improver.SelfImprover(), backend_dir


class TestProveThenWriteContract:
    def test_external_imports_detected(self):
        content = (
            "import requests\n"
            "import numpy as np\n"
            "from . import helpers\n"
            "from chat_helpers import run_with_heartbeat\n"
            "def run(args):\n    return requests.get(args['url'])\n"
        )
        internal = {"chat_helpers", "helpers"}
        external = safe_writer.detect_external_imports(content, internal)
        assert "requests" in external
        assert "numpy" in external
        # Relative + internal imports are NOT external.
        assert "helpers" not in external
        assert "chat_helpers" not in external

    def test_doc_source_derived_from_domain(self):
        # The doc_source safe_write requires is exactly what
        # resolve_doc_domain produces for the external import.
        domain = resolve_doc_domain("requests")
        assert domain == "docs.python-requests.org"
        doc_source = f"https://{domain}/en/latest/"
        assert doc_source.startswith("https://docs.python-requests.org")

    def test_safe_write_accepts_derived_doc_source(self, patched_improver):
        improver, _backend_dir = patched_improver
        content = (
            "import requests\n\ndef run(args):\n    return requests.get(args['url'])\n"
        )
        domain = resolve_doc_domain("requests")
        result = improver.safe_write(
            "vaultbot_backend/my_new_tool.py",
            content,
            doc_source=f"https://{domain}/en/latest/",
        )
        assert result["status"] == "written"
        assert result["checks"]["doc_source"] == "ok"

    def test_safe_write_rejects_without_doc_source(self, patched_improver):
        improver, _backend_dir = patched_improver
        content = (
            "import requests\n\ndef run(args):\n    return requests.get(args['url'])\n"
        )
        result = improver.safe_write("vaultbot_backend/my_new_tool.py", content)
        assert result["status"] == "rejected"
        assert "requests" in result["error"]

    def test_procedure_delegates_to_doc_domains(self):
        # The Check-API-Against-Docs note must import doc_domains, not
        # hardcode the map (Gap 1 acceptance criterion).
        note = _PROCEDURES_DIR / "Check-API-Against-Docs.md"
        assert note.exists(), f"procedure note missing: {note}"
        text = note.read_text(encoding="utf-8")
        assert "from doc_domains import resolve_doc_domain" in text
        # The old hardcoded map must be gone.
        assert "DOC_DOMAINS = {" not in text
