"""Tests for the model capability metadata (get_model_capabilities).

Verifies that the /models endpoint returns enriched objects with vision +
instruct flags, and that get_model_capabilities correctly identifies:
  - embed models as instruct=False
  - text models as instruct=True
  - vision models as vision=True (when projector info is present)

These tests use a mock OllamaClient that returns canned /api/show responses
so they don't depend on Ollama being running.
Run: pytest tests/test_model_capabilities.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


class TestGetModelCapabilities:
    """get_model_capabilities on OllamaClient with mocked /api/show."""

    def _make_client(
        self, show_response: dict | None = None, show_error: Exception | None = None
    ):
        """Build an OllamaClient with a mocked _session.post."""
        from ollama_client import OllamaClient

        client = OllamaClient()
        # Mock the requests.Session.post so /api/show returns canned data.
        mock_resp = MagicMock()
        if show_error:
            mock_resp.raise_for_status.side_effect = show_error
        else:
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = show_response or {}
        client._session = MagicMock()
        client._session.post.return_value = mock_resp
        return client

    def test_embed_model_is_not_instruct(self):
        """nomic-embed-text should return instruct=False (it's an embed model)."""
        show = {
            "model_info": {"bert.embedding_length": 768},
            "details": {"families": ["bert"], "parameter_size": "137M"},
        }
        client = self._make_client(show_response=show)
        caps = client.get_model_capabilities("nomic-embed-text:latest")
        assert caps["instruct"] is False
        assert caps["vision"] is False

    def test_text_model_is_instruct_no_vision(self):
        """Plain text model (no projector, no embed) is instruct, not vision."""
        show = {
            "model_info": {"llama.context_length": 32768},
            "details": {"families": ["llama"], "parameter_size": "8B"},
            "templates": {"chat": "some template"},
        }
        client = self._make_client(show_response=show)
        caps = client.get_model_capabilities("llama3:latest")
        assert caps["instruct"] is True
        assert caps["vision"] is False

    def test_vision_model_has_vision(self):
        """A model with projector_info in the top-level response → vision=True."""
        show = {
            "model_info": {"llama.context_length": 32768},
            "projector_info": {"arch": "clip"},
            "details": {"families": ["llama", "clip"], "parameter_size": "8B"},
        }
        client = self._make_client(show_response=show)
        caps = client.get_model_capabilities("llava:latest")
        assert caps["vision"] is True
        assert caps["instruct"] is True

    def test_vision_model_via_model_info_keys(self):
        """Model with 'vision' keys in model_info (no projector_info) is vision."""
        show = {
            "model_info": {
                "qwen35moe.context_length": 262144,
                "qwen35moe.vision.block_count": 32,
                "qwen35moe.vision_start_token_id": 151652,
            },
            "details": {"families": ["qwen35moe"], "parameter_size": "36B"},
        }
        client = self._make_client(show_response=show)
        caps = client.get_model_capabilities("qwen3.6:latest")
        assert caps["vision"] is True
        assert caps["instruct"] is True

    def test_error_raises(self):
        """On any error (Ollama down, 404), raise — no silent defaults."""
        client = self._make_client(show_error=ConnectionError("refused"))
        with pytest.raises(ConnectionError):
            client.get_model_capabilities("some-model:latest")

    def test_empty_model_returns_defaults(self):
        """Empty string model → safe defaults without calling Ollama."""
        from ollama_client import OllamaClient

        client = OllamaClient()
        client._session = MagicMock()  # ensure no real HTTP call
        caps = client.get_model_capabilities("")
        assert caps == {"vision": False, "instruct": True}
        client._session.post.assert_not_called()
