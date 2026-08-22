"""Tests for the built-in default providers (OpenRouter, browser, edge-tts).

The installer collects an OpenRouter API key during setup, but historically
the registry never seeded an ``openrouter`` provider — the key landed on a
mislabeled "OpenAI" provider (or nowhere), so a user who picked the cloud
path at install couldn't actually use OpenRouter without hand-editing the
registry. These tests pin the fix: OpenRouter is a default provider, and a
legacy OpenRouter base_url migrates to the ``openrouter`` provider with the
key filled in.

Run: pytest tests/test_provider_defaults.py -v
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from providers import ProviderRegistry  # noqa: E402


def _fresh_registry(tmp_path, monkeypatch):
    """Build a registry against a temp providers.json with no legacy env."""
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_LLM_MODEL", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)
    monkeypatch.delenv("SMALL_MODEL", raising=False)
    return ProviderRegistry.migrate_from_env(tmp_path / "providers.json")


def test_openrouter_is_a_default_provider(tmp_path, monkeypatch):
    """A fresh install seeds an ``openrouter`` provider out of the box."""
    reg = _fresh_registry(tmp_path, monkeypatch)
    prov = reg.get_provider("openrouter")
    assert prov is not None, "openrouter must be a default provider"
    assert prov.type == "openai"
    assert "openrouter.ai" in prov.base_url
    assert prov.api_key == ""  # empty until the user supplies a key


def test_browser_and_edge_tts_are_default_providers(tmp_path, monkeypatch):
    """The speech fallback providers are still seeded on a fresh install."""
    reg = _fresh_registry(tmp_path, monkeypatch)
    assert reg.get_provider("browser") is not None
    assert reg.get_provider("edge-tts") is not None


def test_openrouter_key_migrates_to_openrouter_provider(tmp_path, monkeypatch):
    """A legacy OpenRouter base_url + key land on the ``openrouter`` provider,
    not a mislabeled "OpenAI" entry."""
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("LLM_MODEL", "z-ai/glm-5.2:free")
    monkeypatch.delenv("OLLAMA_LLM_MODEL", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)
    monkeypatch.delenv("SMALL_MODEL", raising=False)

    reg = ProviderRegistry.migrate_from_env(tmp_path / "providers.json")

    prov = reg.get_provider("openrouter")
    assert prov is not None
    assert prov.api_key == "sk-or-test-key"
    assert prov.label == "OpenRouter"
    # The big role should point at a model on the openrouter provider.
    big = reg.get_role("big")
    assert big is not None and big.startswith("openrouter:")


def test_openai_base_url_still_migrates_to_openai_provider(tmp_path, monkeypatch):
    """A non-OpenRouter cloud base_url still lands on the ``openai`` provider."""
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com")
    monkeypatch.setenv("LLM_API_KEY", "sk-openai-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("OLLAMA_LLM_MODEL", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)
    monkeypatch.delenv("SMALL_MODEL", raising=False)

    reg = ProviderRegistry.migrate_from_env(tmp_path / "providers.json")

    prov = reg.get_provider("openai")
    assert prov is not None
    assert prov.api_key == "sk-openai-test"
    assert prov.label == "OpenAI"
    big = reg.get_role("big")
    assert big is not None and big.startswith("openai:")
