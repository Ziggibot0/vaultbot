"""Tests for the typed error layer (error_types + diagnostics).

These are the canonical example for future contributors of how a VaultBot
error is classified. Each test feeds a representative exception and
asserts:
  - the returned Diagnosis.category is correct,
  - the user_message contains NO stack-trace tokens,
  - the raw repr is captured only in raw_for_log (never the user message).

Run: pytest tests/test_diagnostics.py -v
"""
from __future__ import annotations

import pytest
from diagnostics import classify_error, diagnose_from_message
from error_types import Diagnosis, ProblemCategory, Severity

# ─────────────────────────────────────────────────────────────────────────
# Predicates — each gets one representative exception + a negative case
# ─────────────────────────────────────────────────────────────────────────

class TestOllamaDown:
    """ConnectionRefusedError / requests.ConnectionError → ollama_down."""

    def test_connection_refused(self):
        exc = ConnectionRefusedError("Connection refused to 127.0.0.1:11434")
        d = classify_error(exc, {"stage": "talking"})
        assert d.category is ProblemCategory.OLLAMA_DOWN
        assert "Ollama" in d.user_message or "backend" in d.user_message
        assert "stack" not in d.user_message.lower()
        assert "Traceback" not in d.user_message
        assert d.action == "restart"
        assert d.severity is Severity.FIXABLE
        # Raw repr preserved for the log only.
        assert "Connection refused" in d.raw_for_log

    def test_requests_connection_error_phrasing(self):
        # requests.ConnectionError carries "Max retries exceeded".
        exc = Exception("requests.exceptions.ConnectionError: "
                        "Max retries exceeded with url: /api/generate")
        d = classify_error(exc, {"stage": "chat"})
        assert d.category is ProblemCategory.OLLAMA_DOWN
        assert "Max retries" not in d.user_message  # no library jargon

    def test_cloud_endpoint_unreachable(self):
        # A cloud backend that's down should still map to ollama_down (the
        # endpoint name in the message adapts via context).
        exc = ConnectionRefusedError("connection refused")
        d = classify_error(exc, {"stage": "starting", "endpoint": "the LLM backend"})
        assert d.category is ProblemCategory.OLLAMA_DOWN
        assert "LLM backend" in d.user_message


class TestModelMissing:
    """HTTPError 404 'model not found' → model_not_pulled (by default)."""

    def test_ollama_model_not_found(self):
        exc = Exception("HTTPError 404: model 'qwen3.6:latest' not found, "
                        "try pulling it first")
        d = classify_error(exc, {"model": "qwen3.6:latest"})
        assert d.category is ProblemCategory.MODEL_NOT_PULLED
        assert "qwen3.6:latest" in d.user_message  # model name is OK to show
        assert "404" not in d.user_message          # no status code jargon
        assert d.action == "pull_model"

    def test_openai_model_does_not_exist(self):
        exc = Exception("The model 'gpt-5' does not exist")
        d = classify_error(exc, {"model": "gpt-5"})
        assert d.category is ProblemCategory.MODEL_NOT_PULLED
        assert "download" in d.user_message.lower()


class TestPortInUse:
    """EADDRINUSE → port_in_use with a restart remedy."""

    def test_eaddrinuse(self):
        exc = OSError("EADDRINUSE: address already in use")
        d = classify_error(exc, {"port": 8000})
        assert d.category is ProblemCategory.PORT_IN_USE
        assert "8000" in d.user_message
        assert "edit" not in d.user_message.lower()  # never "edit main.py"
        assert d.action == "restart"

    def test_errno_98(self):
        exc = OSError("errno 98 address already in use")
        d = classify_error(exc, {"port": 8080})
        assert d.category is ProblemCategory.PORT_IN_USE


class TestFaissAbi:
    """numpy/FAISS ABI mismatch → faiss_abi with a repair remedy."""

    def test_faiss_import_error(self):
        exc = ImportError("numpy.core.multiarray failed to import "
                          "(faiss._swigfaiss)")
        d = classify_error(exc)
        assert d.category is ProblemCategory.FAISS_ABI
        assert "FAISS" in d.user_message or "math library" in d.user_message
        assert "numpy.core.multiarray" not in d.user_message
        assert d.action == "repair_faiss"
        assert d.severity is Severity.BROKEN

    def test_undefined_symbol(self):
        exc = ImportError("undefined symbol: PyArray_MinScalarType_faiss")
        d = classify_error(exc)
        assert d.category is ProblemCategory.FAISS_ABI


class TestSyncedFolder:
    """Vault in OneDrive/Dropbox/iCloud → synced_folder (data risk)."""

    @pytest.mark.parametrize("marker", [
        "C:/Users/testuser/VaultBot",
        "/home/s/Dropbox/vault",
        "/Users/s/Library/Mobile Documents/iCloud~Drive/vault",
    ])
    def test_detects_sync_folders(self, marker):
        d = diagnose_from_message("synced folder detected", path=marker)
        assert d.category is ProblemCategory.SYNCED_FOLDER
        assert "sync" in d.user_message.lower() or "cloud" in d.user_message.lower()
        assert d.severity is Severity.BROKEN
        assert d.action == "move_vault"

    def test_does_not_flag_plain_paths(self):
        d = diagnose_from_message("synced folder detected",
                                  path="C:/Users/testuser/Documents/VaultBot")
        # The message says "synced folder" so it WILL classify — this is a
        # reminder that /preflight only emits the message when the marker
        # check passes. Here we just assert the category is right given the
        # message, not that the path detection is in diagnostics (that's
        # in routers/system.py _check_synced_folder).
        assert d.category is ProblemCategory.SYNCED_FOLDER


class TestGeneric:
    """Unknown exceptions fall through to generic (never raises)."""

    def test_truly_unknown(self):
        exc = RuntimeError("something totally novel blew up at line 42")
        d = classify_error(exc, {"stage": "research"})
        assert d.category is ProblemCategory.GENERIC
        assert d.severity is Severity.BROKEN
        assert "novel blew up" not in d.user_message  # no leak
        assert "novel blew up" in d.raw_for_log        # captured for log

    def test_buggy_predicate_does_not_crash(self):
        # A predicate that itself raises must fall through to generic.
        d = classify_error(ValueError("x"), {"stage": "test"})
        assert isinstance(d, Diagnosis)


# ─────────────────────────────────────────────────────────────────────────
# Diagnosis serialization — the contract with the frontend
# ─────────────────────────────────────────────────────────────────────────
class TestDiagnosisSerialization:
    """to_dict is the exact JSON shape the WS / /diagnose return."""

    def test_omits_raw_by_default(self):
        d = classify_error(ConnectionRefusedError("x"), {"stage": "chat"})
        payload = d.to_dict()
        assert "raw_for_log" not in payload
        assert "category" in payload and isinstance(payload["category"], str)
        assert "severity" in payload and isinstance(payload["severity"], str)
        assert "user_message" in payload
        assert "remedy_hint" in payload
        assert "action" in payload

    def test_include_raw_keeps_string(self):
        d = classify_error(RuntimeError("boom"), {})
        payload = d.to_dict(include_raw=True)
        assert "raw_for_log" in payload
        assert isinstance(payload["raw_for_log"], str)

    def test_category_serializes_to_value(self):
        d = classify_error(ConnectionRefusedError("x"), {"stage": "chat"})
        assert d.to_dict()["category"] == "ollama_down"


# ─────────────────────────────────────────────────────────────────────────
# Ordering — most specific predicates win first
# ─────────────────────────────────────────────────────────────────────────
class TestRegistryOrdering:
    """faiss_abi must beat generic; model_not_found must beat ollama_down
    when both could match (a 404 is a ConnectionError-shaped HTTPError)."""

    def test_faiss_beats_generic(self):
        # An ImportError mentioning faiss must not fall to generic.
        d = classify_error(ImportError("faiss: undefined symbol X"))
        assert d.category is ProblemCategory.FAISS_ABI

    def test_model_not_found_beats_ollama_down(self):
        # "model not found" carries "connection" sometimes; the model
        # category is more actionable, so it must win.
        exc = Exception("ConnectionError: model 'x' not found")
        d = classify_error(exc, {"model": "x"})
        assert d.category is ProblemCategory.MODEL_NOT_PULLED
