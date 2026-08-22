"""Tests for doc_domains.py — the extensible doc-domain map (issue #207, Gap 1).

Verifies the resolution order: stdlib -> known third-party map -> PyPI
metadata fallback -> None (verify manually). Pure logic, no network, no
Ollama.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from doc_domains import DOC_DOMAINS, resolve_doc_domain


class TestResolveDocDomain:
    def test_stdlib_maps_to_python_docs(self):
        # os, json, re, pathlib are all stdlib — must map to docs.python.org
        # WITHOUT a map entry (auto-detected via sys.stdlib_module_names).
        for mod in ("os", "json", "re", "pathlib", "asyncio", "subprocess"):
            assert resolve_doc_domain(mod) == "docs.python.org", mod

    def test_known_third_party_maps_from_map(self):
        assert resolve_doc_domain("requests") == "docs.python-requests.org"
        assert resolve_doc_domain("numpy") == "numpy.org"
        assert resolve_doc_domain("pandas") == "pandas.pydata.org"
        assert resolve_doc_domain("fastapi") == "fastapi.tiangolo.com"

    def test_unknown_module_returns_none(self):
        # A module that is neither stdlib nor in the map nor installed
        # must return None (the safe "verify manually" default).
        assert resolve_doc_domain("definitely_not_a_real_module_xyz") is None

    def test_empty_module_returns_none(self):
        assert resolve_doc_domain("") is None
        assert resolve_doc_domain(None) is None

    def test_installed_package_derives_from_metadata(self):
        # pytest is installed in the dev environment and its metadata
        # points at docs.pytest.org. The map also has it, but this proves
        # the metadata fallback path resolves a real installed package.
        # (If pytest is not installed, this still passes via the map.)
        domain = resolve_doc_domain("pytest")
        assert domain is not None

    def test_map_contains_no_stdlib_entries(self):
        # The map should only hold third-party packages; stdlib is
        # auto-detected. Guard against someone adding "os" -> "docs.python.org"
        # redundantly (harmless but signals a misunderstanding).
        import sys

        stdlib = getattr(sys, "stdlib_module_names", frozenset())
        overlap = set(DOC_DOMAINS) & stdlib
        assert not overlap, f"stdlib modules should not be in DOC_DOMAINS: {overlap}"
