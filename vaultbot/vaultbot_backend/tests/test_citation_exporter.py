"""Tests for citation_exporter.py — DOI extraction + BibTeX/RIS generation.

Pure unit tests: no I/O, no network, no FAISS. Only
``test_export_citations_from_note`` uses a ``tmp_path`` fixture to write
a throwaway note file.
"""

import pytest

pytestmark = pytest.mark.unit

from citation_exporter import extract_doi, to_bibtex, to_ris, export_citations


# --- DOI extraction ---------------------------------------------------------


def test_extract_doi_doi_org():
    assert (
        extract_doi("https://doi.org/10.1038/s41597-024-03668-4")
        == "10.1038/s41597-024-03668-4"
    )


def test_extract_doi_nature():
    assert (
        extract_doi("https://nature.com/articles/s41597-024-03668-4")
        == "10.1038/s41597-024-03668-4"
    )


def test_extract_doi_arxiv():
    assert (
        extract_doi("https://arxiv.org/abs/2404.16130") == "10.48550/arXiv.2404.16130"
    )


def test_extract_doi_arxiv_pdf():
    assert (
        extract_doi("https://arxiv.org/pdf/2404.16130") == "10.48550/arXiv.2404.16130"
    )


def test_extract_doi_springer():
    assert (
        extract_doi("https://springer.com/article/10.1007/s12345-024-00678-9")
        == "10.1007/s12345-024-00678-9"
    )


def test_extract_doi_no_match():
    assert extract_doi("https://example.com/page") is None


# --- BibTeX -----------------------------------------------------------------


def test_to_bibtex_basic():
    sources = [{"title": "A Test Page", "url": "https://example.com/page"}]
    out = to_bibtex(sources)
    assert "@misc{" in out
    assert "A Test Page" in out
    assert "https://example.com/page" in out
    assert "urldate" in out
    # no DOI → no doi field
    assert "doi " not in out


def test_to_bibtex_with_doi():
    sources = [
        {
            "title": "A Nature Article",
            "url": "https://nature.com/articles/s41597-024-03668-4",
            "doi": "10.1038/s41597-024-03668-4",
        }
    ]
    out = to_bibtex(sources)
    assert "@article{" in out
    assert "doi          = {10.1038/s41597-024-03668-4}" in out
    assert "A Nature Article" in out


# --- RIS --------------------------------------------------------------------


def test_to_ris_basic():
    sources = [{"title": "A Test Page", "url": "https://example.com/page"}]
    out = to_ris(sources)
    assert "TY  - ELEC" in out
    assert "TI  - A Test Page" in out
    assert "UR  - https://example.com/page" in out
    assert out.rstrip().endswith("ER  -")


def test_to_ris_with_doi():
    sources = [
        {
            "title": "Article",
            "url": "https://nature.com/articles/s41597-024-03668-4",
            "doi": "10.1038/s41597-024-03668-4",
        }
    ]
    out = to_ris(sources)
    assert "TY  - JOUR" in out
    assert "DO  - 10.1038/s41597-024-03668-4" in out


# --- Full note parsing + export --------------------------------------------


def test_export_citations_from_note(tmp_path):
    note = tmp_path / "topic.md"
    note.write_text(
        """---
type: research
---

# Topic

Some prose.

## Sources

- [First Source](https://doi.org/10.1038/s41597-024-03668-4)
- [Second Source](https://example.com/page)
""",
        encoding="utf-8",
    )
    out = export_citations(str(note))
    assert "@article{" in out  # doi.org source → academic + doi
    assert "10.1038/s41597-024-03668-4" in out
    assert "@misc{" in out  # example.com → misc
    assert "Second Source" in out
