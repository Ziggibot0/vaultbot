"""HTML parser for textbook_ingest.

Extracted from ``custom_tools.textbook_ingest``. Converts HTML content
into sections by walking the DOM tree, collecting heading and content
elements, and rendering them to Markdown via ``html2text``.
"""

import re


def parse_html(content_text, base_url=""):
    """Parse HTML into sections by headings."""
    import html2text
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(content_text, "lxml")

    # Try to find main content area (try multiple strategies)
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", {"class": "mw-parser-output"})
        or soup.find("div", {"id": "content"})
        or soup.find("div", {"class": "page-content"})
        or soup
    )

    h2t = html2text.HTML2Text()
    h2t.body_width = 0
    h2t.ignore_images = True
    h2t.ignore_links = False
    h2t.unicode_snob = True

    # Collect headings and content elements in document order
    elements = []
    for elem in main.descendants:
        if not isinstance(elem, Tag):
            continue

        # Skip elements inside unwanted containers
        in_unwanted = False
        p = elem.parent
        while p:
            if p.get("class"):
                classes = set(p.get("class", []))
                if any(
                    x in classes
                    for x in [
                        "infobox",
                        "sidebar",
                        "navbox",
                        "vertical-navbox",
                        "reflist",
                        "references",
                        "mw-editsection",
                        "toc",
                        "hatnote",
                        "ambox",
                        "mw-jump-link",
                        "printfooter",
                        "catlinks",
                        "mw-navigation",
                    ]
                ):
                    in_unwanted = True
                    break
            if p.name == "table":
                in_unwanted = True
                break
            p = p.parent
        if in_unwanted:
            continue

        if elem.name in ("h1", "h2", "h3", "h4"):
            text = elem.get_text(strip=True).replace("[edit]", "").strip()
            if text and len(text) > 1:
                elements.append(("heading", text, int(elem.name[1]), elem))
        elif elem.name in ("p", "ul", "ol", "blockquote"):
            elements.append(("content", "", 0, elem))

    # Build sections
    sections = []
    current_heading = None
    current_level = 0
    current_parts = []

    for etype, text, level, tag in elements:
        if etype == "heading":
            if current_heading is not None:
                sections.append(
                    {
                        "heading": current_heading,
                        "level": current_level,
                        "content": "\n".join(current_parts),
                    }
                )
            current_heading = text
            current_level = level
            current_parts = []
        elif etype == "content":
            md = h2t.handle(str(tag))
            md = re.sub(r"\[edit\]\([^)]*\)", "", md)
            md = re.sub(r"\[Edit section[^]]*\]\([^)]*\)", "", md)
            if md.strip():
                current_parts.append(md.strip())

    if current_heading is not None:
        sections.append(
            {
                "heading": current_heading,
                "level": current_level,
                "content": "\n".join(current_parts),
            }
        )

    # Filter: only keep sections with real content
    # Lazy import to avoid circular dependency:
    # textbook_ingest imports this module, and this function needs
    # is_toc_entry which lives in textbook_ingest.
    from custom_tools.textbook_ingest import is_toc_entry

    sections = [s for s in sections if len(s["content"]) > 50]
    sections = [s for s in sections if not is_toc_entry(s["content"])]

    # Extract title
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"
    title = re.sub(r"\s*-\s*Wikipedia\s*$", "", title)
    title = re.sub(r"\s*\|\s*.*$", "", title)

    return title, sections
