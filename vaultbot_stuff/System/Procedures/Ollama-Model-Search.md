---
type: procedure
status: active
model_cartridge: small
created: 2026-08-01
description: "Search Ollama's model library, check available tags, list installed models, and pull new models. Uses the ollama_model_search tool."
when_to_use: "When you need to find, evaluate, or pull Ollama models — e.g. looking for a vision model, a small local model, or checking what's installed."
allowed_tools: [ollama_model_search]
---

# Ollama Model Search

Search, evaluate, and pull models from Ollama's library using the `ollama_model_search` tool. This procedure covers the full workflow: find a model → check available sizes/tags → pull the right one → verify it's installed.

## Step 1: Search the Library

Find models matching a query. Use `action=search` with a keyword.

```
Call ollama_model_search with:
  action: "search"
  query: "<your search term, e.g. 'vision' or 'embedding' or 'reasoning'>"
  category: "<optional: vision, tools, embedding, reasoning>"
```

The tool scrapes `https://ollama.com/search` and returns model cards with name, description, pull count, and update date. Review the results to identify candidate models.

## Step 2: Check Available Tags

Once you've identified a model, check what sizes/variants are available before pulling. Use `action=tags` with the model name.

```
Call ollama_model_search with:
  action: "tags"
  query: "<model name, e.g. 'llama3.2' or 'qwen2.5'>"
```

This returns all pullable tags (e.g. `1.5b`, `3b`, `7b`, `fp16`, `q4_K_M`) with sizes. Pick the tag that fits your hardware constraints.

## Step 3: Check What's Already Installed

Before pulling, verify you don't already have the model. Use `action=installed`.

```
Call ollama_model_search with:
  action: "installed"
```

This runs `ollama list` locally and returns all installed models with their size and modification date. If the model is already there, skip to Step 5.

## Step 4: Pull the Model

Pull the chosen model with a specific tag (or `latest` if no tag specified).

```
Call ollama_model_search with:
  action: "pull"
  query: "<model name>"
  tag: "<specific tag, e.g. '3b' or '1.5b-q4_K_M'>"
```

This runs `ollama pull <model>:<tag>` and returns the cleaned output (ANSI escape sequences stripped). Pull time depends on model size and network speed.

## Step 5: Verify Installation

Confirm the model landed correctly.

```
Call ollama_model_search with:
  action: "installed"
```

Check that the newly pulled model appears in the list with the expected size.

## Decision Notes

- **Small models for procedures:** When pulling a model to serve as a `model_cartridge: small` backend, prefer quantized variants (`q4_K_M`, `q5_K_M`) in the 0.5B–3B parameter range. These run fast on CPU and save cloud tokens.
- **Vision models:** If the task involves image/PDF reading, filter search with `category: vision`.
- **Embedding models:** For retrieval-augmented generation, filter with `category: embedding`.
- **Disk space:** Check available disk before pulling large models (7B+ models can be 4–8 GB each).

## Related

- [[Write-Python-Tool]] — how the `ollama_model_search` tool was created
- [[Tool-vs-Procedure-Decision-Guide]] — why this is a procedure, not a tool