---
type: procedure
status: verified
model_cartridge: small
created: 2026-07-31
description: Read one page of an ingested textbook PDF and get its content as text. The page is rendered to an image and read by a vision model so equations, figures, and tables come through exactly as printed.
when: When reading a specific page from an ingested textbook
allowed_tools:
  - vault_search
summary: TEXTBOOK_READ_PAGE_TOOL_VISION_EXACT_TEXT_SIMULATION

| textbook_read_page,tool_vision_extract,exact_math_logic |
tags:
  - procedure
  - procedures
---

# Textbook-Read-Page

Read one page of an ingested textbook PDF and get its content as text. The page is rendered to an image and read by a vision-capable model so equations, figures, and tables come through exactly as printed — unlike a text-layer extract which drops vector-drawn math.

## Steps

1. ```python
   # Call the textbook_read_page tool's run() function. It needs an
   # (optional) vision-capable llm_client for the precise image path;
   # without one it falls back to the PDF text layer (with a caveat).
   from custom_tools.textbook_read_page import run as _read_page
   _vision = None
   try:
       from llm_client import get_vision_client as _gv
       _vision = _gv()
   except Exception:
       _vision = None
   result = _read_page({"pdf": args.get("pdf", ""), "page": args.get("page", 1)},
                       llm_client=_vision)
   print(result)
   ```