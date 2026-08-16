"""
textbook_read_page — the on-demand page reader.

This is the other half of the index-only paradigm. The ingest step built a
TOC of pointers (heading → page). When the user asks about a topic, the LLM
finds the relevant page in the TOC and calls THIS tool to actually read it.

The tool renders the requested page of the source PDF to an image and sends
it to a vision-capable model, which transcribes/summarizes what it sees —
equations, figures, and all, exactly as a human would read the page. The
result is the page's content as clean text the LLM can reason about and cite.

Why this beats pre-extraction: the equations are vector-drawn, so any text
layer drops them. But a vision model looking at the RENDERED page sees them
perfectly. And we only ever read the ONE page the LLM needs — not the whole
book — so the image-token cost is tiny (a few hundred tokens per page read).

The LLM then writes a note into the vault capturing what it learned, with
provenance: "> source: [[book.pdf]] page 22". That note is a clean, small,
LLM-curated unit — exactly the file-unit principle. Nothing is auto-written;
the graph only ever contains things the model actually read and chose to
record.
"""

import base64
from pathlib import Path
from typing import Any

SCHEMA = {
    "name": "textbook_read_page",
    "description": (
        "Read one page of an ingested textbook PDF and get its content as "
        "text. Use this when the user asks about a topic that the vault's "
        "index TOC points to a specific page for. The page is rendered to an "
        "image and read by a vision model, so equations, figures, and tables "
        "come through exactly as printed — unlike a text-layer extract which "
        "drops vector-drawn math. After reading, write what you learned into "
        "the vault as a note with provenance (source PDF + page number)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pdf": {
                "type": "string",
                "description": (
                    "The bare filename of the source PDF in "
                    "learningMaterial/ (e.g. "
                    "'calculus-volume-1_-_WEB.pdf'). Use vault_list or "
                    "code_read on learningMaterial/ to find the exact "
                    "filename if unsure."
                ),
            },
            "page": {
                "type": "integer",
                "description": "The 1-indexed page number to read.",
            },
        },
        "required": ["pdf", "page"],
    },
}

# textbook_read_page.py lives in vaultbot_backend/custom_tools/, so
# parent = custom_tools, parent.parent = vaultbot_backend, parent.parent.parent
# = vaultbot/ (the framework root).
try:
    VAULT_DIR = (
        Path(__file__).resolve().parent.parent.parent
    )  # vaultbot/ (framework root, 3 levels up from vaultbot/vaultbot_backend/custom_tools/)
except NameError:
    VAULT_DIR = Path(".").resolve()
BACKEND_DIR = VAULT_DIR / "vaultbot_backend"
LEARNING_DIR = VAULT_DIR / "learningMaterial"
TEXTBOOKS_DIR = VAULT_DIR / "Knowledge" / "Textbooks"


def _resolve_pdf(pdf_name: str) -> Path | None:
    """Resolve a PDF reference to an actual path.

    Accepts: a bare filename, a path under learningMaterial/, or an index
    TOC note name (e.g. 'calculus-volume-1-index' -> find the PDF).
    """
    pdf_name = pdf_name.strip()
    # Strip -index / -index.md suffix to look up the TOC's source PDF.
    if pdf_name.endswith("-index.md"):
        pdf_name = pdf_name[: -len("-index.md")]
    if pdf_name.endswith("-index"):
        # Find the TOC note, read its Source PDF line.
        toc = TEXTBOOKS_DIR / ("%s.md" % pdf_name) if TEXTBOOKS_DIR.exists() else None
        if toc and toc.exists():
            import re

            text = toc.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"Source PDF:\*\*\s*(.+)", text)
            if m:
                rel = m.group(1).strip()
                p = (VAULT_DIR / rel).resolve()
                if p.exists():
                    return p
    # Bare filename in learningMaterial/.
    candidate = LEARNING_DIR / pdf_name
    if candidate.exists():
        return candidate
    # Try matching by stem if the exact name differs slightly.
    if LEARNING_DIR.exists():
        stem = Path(pdf_name).stem
        for f in LEARNING_DIR.glob("*.pdf"):
            if f.stem == stem or stem in f.stem:
                return f
    return None


def _render_page_image(pdf_path: Path, page_num: int, dpi: int = 150) -> str | None:
    """Render one PDF page to a base64 PNG (for the vision model).

    dpi=150 balances legibility vs token cost (~800-1200 tokens/page at
    OpenAI's high-detail tiling). Returns None on any failure.
    """
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return None
        page = doc[page_num - 1]  # 1-indexed input
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()
        return base64.b64encode(png_bytes).decode("ascii")
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return None


def _extract_page_text(pdf_path: Path, page_num: int) -> str:
    """Fast text-layer extract for one page (fallback when no vision model)."""
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return ""
        text = doc[page_num - 1].get_text("text")
        doc.close()
        return text
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return ""


def run(args: dict[str, Any], llm_client=None) -> dict[str, Any]:
    """Read one page of a textbook PDF.

    Renders the page to an image and asks the vision-capable llm_client to
    transcribe it. If the client can't see images (or none is provided),
    falls back to the text-layer extract (which may drop equations) and says
    so explicitly so the LLM knows the math may be missing.
    """
    pdf_ref = args.get("pdf", "")
    page = int(args.get("page", 0))
    if not pdf_ref or page < 1:
        return {"error": "both 'pdf' and 'page' (>=1) are required"}

    pdf_path = _resolve_pdf(pdf_ref)
    if pdf_path is None or not pdf_path.exists():
        return {"error": "PDF not found: %s (looked in learningMaterial/)" % pdf_ref}

    img_b64 = _render_page_image(pdf_path, page)
    if img_b64 is None:
        return {"error": "could not render page %d of %s" % (page, pdf_path.name)}

    # Try the vision path first (precise — sees equations).
    if llm_client is not None:
        try:
            capable = llm_client.vision_capable()
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            capable = False
        if capable:
            content = _read_with_vision(llm_client, img_b64, pdf_path.name, page)
            if content:
                return {
                    "status": "ok",
                    "pdf": pdf_path.name,
                    "page": page,
                    "content": content,
                    "method": "vision",
                    "provenance": "%s page %d" % (pdf_path.name, page),
                }
        # Vision unavailable — fall through to text layer with a caveat.
        text = _extract_page_text(pdf_path, page)
        return {
            "status": "ok",
            "pdf": pdf_path.name,
            "page": page,
            "content": text or "(no text on this page)",
            "method": "text_layer",
            "caveat": (
                "Read via the text layer because the active model "
                "can't see images. Vector-drawn equations and figures "
                "may be MISSING from this text. Pick a vision model "
                "in Settings for complete page reads."
            ),
            "provenance": "%s page %d" % (pdf_path.name, page),
        }

    # No llm_client at all — text layer only.
    text = _extract_page_text(pdf_path, page)
    return {
        "status": "ok",
        "pdf": pdf_path.name,
        "page": page,
        "content": text or "(no text on this page)",
        "method": "text_layer",
        "caveat": "No vision model configured; equations may be missing.",
        "provenance": "%s page %d" % (pdf_path.name, page),
    }


def _read_with_vision(llm_client, img_b64: str, pdf_name: str, page: int) -> str:
    """Ask the vision-capable llm_client to transcribe a page image.

    Builds an OpenAI-compatible image message and calls the client's chat.
    Works for both OpenAICompatibleClient and OllamaClient (which accept
    images via the per-message `images` field).
    """
    prompt = (
        "This is page %d of the textbook '%s'. Transcribe the page content "
        "faithfully as clean markdown — preserve all equations (use LaTeX "
        "$...$ for inline and $$...$$ for display math), headings, tables, "
        "and figure captions. Do not summarize; transcribe. If a figure is "
        "purely decorative, note '[figure]' but keep its caption."
    ) % (page, pdf_name)

    # Ollama uses the `images` field on the message; OpenAI uses image_url
    # content parts. Detect which by the client type.
    from llm_client import OpenAICompatibleClient

    if isinstance(llm_client, OpenAICompatibleClient):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,%s" % img_b64},
                    },
                ],
            }
        ]
    else:
        # Ollama-style: content string + images list.
        messages = [
            {
                "role": "user",
                "content": prompt,
                "images": [img_b64],
            }
        ]
    try:
        result = llm_client.chat(messages, temperature=0.0, stream=False)
        if isinstance(result, dict):
            return result.get("response", "") or ""
        # A generator (stream=True default on some clients) — drain it.
        if hasattr(result, "__iter__"):
            return "".join(c.get("response", "") for c in result)
        return ""
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return ""
