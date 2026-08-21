"""Regression tests for source_classification.py — source-authority
allowlist/denylist (issue #133).

The research engine must enforce source-quality constraints: when the user
requires authoritative-only sources ("ONLY Google official docs"), a
non-allowlisted domain (a personal blog) must be discarded before synthesis.

Pure functions, no network, no Ollama.
"""

from __future__ import annotations

import pytest
from source_classification import (
    _hostname,
    is_allowlisted,
    is_denylisted,
)

pytestmark = pytest.mark.unit


class TestHostname:
    def test_strips_scheme_and_path(self):
        assert (
            _hostname(
                "https://developers.google.com/identity/protocols/oauth2/native-app"
            )
            == "developers.google.com"
        )

    def test_strips_www(self):
        assert _hostname("https://www.google.com/x") == "google.com"

    def test_strips_port(self):
        assert _hostname("http://example.com:8080/x") == "example.com"

    def test_empty(self):
        assert _hostname("") == ""
        assert _hostname(None) == ""

    def test_malformed(self):
        assert _hostname("not a url") == ""


class TestIsAllowlisted:
    def test_empty_allowlist_allows_all(self):
        assert is_allowlisted("https://anything.com/x", None) is True
        assert is_allowlisted("https://anything.com/x", []) is True

    def test_exact_domain_match(self):
        assert (
            is_allowlisted("https://developers.google.com/x", ["developers.google.com"])
            is True
        )

    def test_subdomain_matches_parent(self):
        # developers.google.com is a subdomain of google.com
        assert is_allowlisted("https://developers.google.com/x", ["google.com"]) is True

    def test_non_matching_domain_rejected(self):
        # The exact bug from issue #133: a personal blog must NOT pass an
        # allowlist of Google official docs.
        assert is_allowlisted("https://melmanm.github.io/x", ["google.com"]) is False

    def test_suffix_boundary_not_fooled(self):
        # "notgoogle.com" must NOT match "google.com" (suffix match, not
        # substring match).
        assert is_allowlisted("https://notgoogle.com/x", ["google.com"]) is False

    def test_www_prefix_matches(self):
        assert is_allowlisted("https://www.google.com/x", ["google.com"]) is True

    def test_multiple_domains(self):
        allow = ["developers.google.com", "googleapis.github.io"]
        assert is_allowlisted("https://googleapis.github.io/x", allow) is True
        assert is_allowlisted("https://medium.com/x", allow) is False


class TestIsDenylisted:
    def test_empty_denylist_blocks_nothing(self):
        assert is_denylisted("https://medium.com/x", None) is False
        assert is_denylisted("https://medium.com/x", []) is False

    def test_denylisted_domain_blocked(self):
        assert is_denylisted("https://medium.com/x", ["medium.com"]) is True

    def test_personal_blog_blocked(self):
        # The exact source from issue #133 (melmanm.github.io).
        assert is_denylisted("https://melmanm.github.io/x", ["github.io"]) is True

    def test_non_denylisted_allowed(self):
        assert is_denylisted("https://developers.google.com/x", ["medium.com"]) is False

    def test_subdomain_matches(self):
        assert is_denylisted("https://blog.medium.com/x", ["medium.com"]) is True
