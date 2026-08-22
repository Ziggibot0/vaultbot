"""Tests for the SSRF guard on provider base_urls (issue #253).

Verifies that ``assert_public_base_url`` rejects base_urls that resolve to
private/metadata addresses (the SSRF + credential-exfil vector) while still
allowing loopback (local Ollama / LM Studio) and public endpoints.

Run: pytest tests/test_ssrf_guard.py -v
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from providers import assert_public_base_url  # noqa: E402


class TestAssertPublicBaseUrl:
    """The SSRF guard must reject metadata/private targets, allow public."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254",  # cloud metadata (AWS/GCP/Azure)
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5",  # private
            "http://10.1.2.3/v1",
            "http://172.16.0.1",  # private
            "http://172.31.255.255",
            "http://192.168.1.1",  # private
            "http://192.168.0.10/api",
            "http://100.64.0.1",  # CGNAT
            "http://0.0.0.0",  # "this" network
            "http://metadata.google.internal",  # GCP metadata hostname
            "http://metadata",  # bare metadata hostname
            "http://[fc00::1]",  # unique-local (private) IPv6
            "http://[fe80::1]",  # link-local IPv6
        ],
    )
    def test_rejects_private_and_metadata(self, url):
        with pytest.raises(ValueError):
            assert_public_base_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434",  # local Ollama — allowed
            "http://127.0.0.1:11434",  # local Ollama — allowed
            "http://localhost:1234",  # LM Studio — allowed
            "http://127.0.0.1:1234",
            "http://[::1]:11434",  # IPv6 loopback — allowed
            "https://api.openai.com",  # public
            "https://openrouter.ai/api",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "https://api.groq.com/openai",
        ],
    )
    def test_allows_loopback_and_public(self, url):
        # Should not raise.
        assert_public_base_url(url)

    def test_rejects_missing_host(self):
        with pytest.raises(ValueError):
            assert_public_base_url("not-a-url")
