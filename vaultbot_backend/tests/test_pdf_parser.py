"""Tests for the pypdf fallback used by textbook ingestion."""

from types import SimpleNamespace

import pytest
from custom_tools.parsers import pdf_parser
from pypdf import PdfWriter

pytestmark = pytest.mark.unit


def test_parse_pdf_regex_reads_real_pypdf_document(tmp_path):
    pdf_path = tmp_path / "example.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": "Example Textbook"})
    writer.write(pdf_path)

    title, sections = pdf_parser._parse_pdf_regex(pdf_path)

    assert title == "PDF Document"
    assert sections == []


def test_parse_pdf_regex_extracts_pages_and_metadata(monkeypatch):
    body = "This section explains the topic in enough detail. " * 8
    pages = [
        SimpleNamespace(extract_text=lambda: f"Chapter 1 Introduction\n{body}"),
        SimpleNamespace(extract_text=lambda: f"Chapter 2 Core Concepts\n{body}"),
        SimpleNamespace(extract_text=lambda: f"Chapter 3 Applications\n{body}"),
    ]
    reader = SimpleNamespace(
        pages=pages,
        metadata=SimpleNamespace(title="Example Textbook"),
    )
    fake_pypdf = SimpleNamespace(PdfReader=lambda file_path: reader)
    monkeypatch.setitem(__import__("sys").modules, "pypdf", fake_pypdf)

    title, sections = pdf_parser._parse_pdf_regex("example.pdf")

    assert title == "Example Textbook"
    assert [section["heading"] for section in sections] == [
        "Chapter 1 Introduction",
        "Chapter 2 Core Concepts",
        "Chapter 3 Applications",
    ]
    assert all(len(section["content"]) > 200 for section in sections)
