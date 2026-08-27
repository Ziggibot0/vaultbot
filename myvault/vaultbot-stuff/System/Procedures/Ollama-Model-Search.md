---
type: procedure
status: active
baseline: true
created: 2026-08-01
description: Search Ollama's model library, check available tags, list installed models, and pull new models. Uses the ollama_model_search tool.
when_to_use: when you need to find, evaluate, compare, or pull Ollama models — looking for a vision model, a small local model, or checking what's installed before a model swap
applies_to:
  - models
  - ollama
  - cartridge
allowed_tools: []
summary: Dispatching an ollama_model_search action with the 'search' payload scrapes local models to identify top candidates based on size, tag compatibility, or user-defined criteria before executing network 
tags:
  - procedure
  - procedures
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Ollama Model Search

Dispatch to the `ollama_model_search` custom tool. The `action` argument
selects the operation; `query`/`tag`/`category` refine it:

| action | required args | what it does |
|---|---|---|
| `search` | `query` | scrape ollama.com/search for matching model cards |
| `tags` | `query` (model name) | list pullable tags/sizes for a model |
| `installed` | — | run `ollama list` (installed models + sizes) |
| `pull` | `query` (model), optional `tag` | `ollama pull <model>:<tag>` |

If no `action` arg is given, default to `installed` (cheapest — shows
what's already local before any network call).

## Why This Exists

Finding, evaluating, and pulling Ollama models requires dispatching to the
`ollama_model_search` tool with the right action. This procedure wraps that
dispatch so the model can search, list tags, check installed models, or pull
new ones. The tradeoff: it defaults to the cheapest action (`installed`) to
avoid network calls before checking what's already local.

## Steps

### Step 1: Dispatch the requested ollama_model_search action

1. ```python
from custom_tools.ollama_model_search import run as _oms

action = args.get("action", "installed")
payload = {"action": action}
if args.get("query"):
    payload["query"] = args["query"]
if args.get("tag"):
    payload["tag"] = args["tag"]
if args.get("category"):
    payload["category"] = args["category"]

result = _oms(payload)
print(result)
```

### Step 2: Interpret the results

2. [llm: Interpret the ollama_model_search output for the user based on the action. For `search`: list candidate models with pull counts and one-line fit-for-purpose notes. For `tags`: list available sizes/tags and recommend one for the user's hardware (respect the 30B memory budget — ~32GB total, a 30B Q4_K_M already uses ~26GB). For `installed`: report what's installed and flag near-duplicates. For `pull`: confirm success and report the pulled model's size. Keep it concise and actionable.]

## Decision Notes

- **Small models for procedures:** For a `model_cartridge: small` backend, prefer quantized variants (`q4_K_M`, `q5_K_M`) in the 0.5B–3B range.
- **Vision models:** filter search with `category: vision`.
- **Embedding models:** filter with `category: embedding`.
- **Disk space:** check free space before pulling 7B+ models (4–8 GB each).

## Related

- [[Write-Python-Tool]] — how the `ollama_model_search` tool was created
- [[Tool-vs-Procedure-Decision-Guide]] — why this is a procedure, not a tool