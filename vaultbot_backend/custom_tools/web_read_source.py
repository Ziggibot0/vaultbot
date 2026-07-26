"""
web_read_source — re-read a saved web source on demand.

Parallel to textbook_read_page, but for web sources the research engine
archived in learningMaterial/web/. The LLM calls this when it needs to
re-examine a source it (or the autonomous researcher) previously scraped —
to verify a claim, pull a quote, or answer a follow-up without re-scraping
(the page may have changed or gone offline).

Extracts clean article text from the saved HTML. If a vision-capable model is
available and the page has figures/equations worth seeing, a future extension
can render the HTML to an image; for now, text extraction is complete and
stable (the raw HTML is preserved on disk, so nothing is lost).
"""

from pathlib import Path
from typing import Any

SCHEMA = {
    "name": "web_read_source",
    "description": (
        "Re-read a web source that was previously archived during research. "
        "Use this when you need to re-examine a source (verify a claim, pull "
        "a quote, answer a follow-up) without re-scraping — the saved copy is "
        "a stable snapshot. Pass the source URL or the archived filename. "
        "Returns the page's article text. After reading, cite it in your note "
        "with provenance to the archived file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The source URL to look up in the archive.",
            },
            "file": {
                "type": "string",
                "description": (
                    "Alternatively, the archived filename in "
                    "learningMaterial/web/ (e.g. 'example-com-1a2b3c4d.html')."
                ),
            },
        },
    },
}

try:
    BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
except NameError:
    BACKEND_DIR = Path(".").resolve()
VAULT_DIR = BACKEND_DIR.parent


def run(args: dict[str, Any], llm_client=None) -> dict[str, Any]:
    """Read a saved web source.

    Accepts either a URL (looked up in the index) or a filename (read
    directly). Returns the article text + provenance.
    """
    import sys
    sys.path.insert(0, str(BACKEND_DIR))
    from web_source_store import find_source, read_source_text, source_path

    url = (args.get("url") or "").strip()
    filename = (args.get("file") or "").strip()
    if not url and not filename:
        return {"error": "provide 'url' or 'file'"}

    entry = None
    if url:
        entry = find_source(url)
        if entry is None:
            return {"error": "no archived source for URL: %s" % url}
        filename = entry["file"]

    path = source_path(filename)
    if not path.exists():
        return {"error": "archived file not found: %s" % filename}

    text = read_source_text(filename)
    if not text:
        return {"error": "could not extract text from %s" % filename}

    return {
        "status": "ok",
        "url": (entry or {}).get("url", ""),
        "file": filename,
        "title": (entry or {}).get("title", ""),
        "content": text,
        "provenance": "learningMaterial/web/%s" % filename,
    }
