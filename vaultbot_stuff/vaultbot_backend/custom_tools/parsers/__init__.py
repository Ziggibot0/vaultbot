"""Format-specific parsers for textbook_ingest.

Each parser takes raw content (HTML, PDF, Markdown, or plain text) and
returns ``(title, sections)`` where *sections* is a list of dicts with
keys ``heading``, ``level``, and ``content``.

Modules:
    html_parser     -- parse_html
    markdown_parser -- parse_markdown + structural heading detection
                       utilities (shared by PDF and text parsers)
    pdf_parser      -- parse_pdf, _parse_pdf_regex
    text_parser     -- parse_plain_text, fragment_section, fragment_sections
"""
