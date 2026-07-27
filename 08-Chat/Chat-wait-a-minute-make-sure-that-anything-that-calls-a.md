# Chat: wait a minute make sure that anything that calls a

## 2026-07-27 04:47 UTC

**User:** wait a minute make sure that anything that calls an LLM is calling it through the configured model NOT a direct connection to the ollama endpoint, otherwise people who use other API's won't be able to use vaultbot

**Assistant:** Clean. Here's the final report:

## Bottom Line

**Fixed 4 issues. All LLM calls now route through `get_llm_client()` — works with Ollama, OpenAI, OpenRouter, any OpenAI-compatible API.**

## What I Fixed

| File | Issue | Fix |
|---|---|---|
| `self_improver.py` L708 | `self_reflect()` created `OllamaClient()` directly — would fail for OpenAI users | Replaced with `get_llm_client()` |
| `note_creator.py` L29 | Created `OllamaClient` but never used it — dead code | Removed import and attribute |
| `routers/system.py` L24 | `_ping_ollama()` hit `/api/version` (Ollama-specific endpoint) — would report false for OpenAI users | Now uses `svc.ollama_client.is_running()` |
| `Dream-Pass.md` Step 3 | Health check used raw socket to `localhost:11434` — would skip LLM synthesis for OpenAI users | Now uses `get_llm_client().is_running()` |

## What's Already Correct

- **`main.py`** — uses `get_llm_client()` to create the synthesis client ✓
- **`step_gate_runtime.py`** — `llm_generate` preamble uses `get_llm_client()` ✓
- **`chat_handler.py`** — uses `get_llm_client()` ✓
- **`routers/llm.py`** — `_rebuild_llm_client()` uses `get_llm_client()` ✓
- **`claim_verifier.py`** — receives `llm_client` from main.py ✓
- **`graph_ops.py`** — receives `ollama_client` from main.py ✓
- **`textbook_read_page.py`** — receives `llm_client` as parameter ✓
- **`vault_indexer.py`** — uses `OllamaClient` for **embeddings only** (by design — `nomic-embed-text` is a separate concern from the chat/synthesis LLM)

## Remaining `OllamaClient()` Calls (All Legitimate)

1. `llm_client.py:425` — inside `get_llm_client()` factory, only when `LLM_BACKEND=ollama`
2. `llm_client.py:486` — inside `get_vision_client()` factory, only when `VISION_BACKEND=ollama`
3. `vault_indexer.py:68` — embeddings only (separate concern, by design)

Dream-Pass still passes all 6 steps after the fix. Step 3 even wrote 2 new semantic notes this run.

<details>
<summary>Thinking process</summary>

Sean wants me to make sure that all LLM calls go through the configured model (whatever API the user has set up), not directly to the Ollama endpoint. This is an important architectural concern — if someone uses OpenAI, Anthropic, or another API, VaultBot should work for them too.

Let me audit the codebase to find all places where LLM calls are made. I need to check:

1. The `llm_client.py` module — how does it work?
2. The `step_gate_runtime.py` preamble — how does `llm_generate` work?
3. The `chat_handler.py` — how does it call the LLM?
4. Any other modules that make LLM calls

Let me start by searching for direct Ollama connections.I need to audit the codebase. Let me search for all files that make LLM calls, looking for direct Ollama connections (like `requests.post` to localhost:11434, or `ollama` library calls, or `httpx` calls to the Ollama endpoint). The key files to check:

1. `llm_client.py` — the main LLM client
2. `step_gate_runtime.py` — the `llm_generate` preamble
3. `chat_handler.py` — the main chat handler
4. `pattern_extractor.py` — might call LLM
5. `claim_verifier.py` — might call LLM
6. `calibration.py` — might call LLM
7. `rag_eval.py` — might call LLM
8. `textbook_read_page` tool — uses vision model
9. `textbook_ingest` tool — might call LLM
10. Any autonomous researcher code

Let me search for direct Ollama connections across all Python files.OK, so the key files that directly reference Ollama are:

1. **`ollama_client.py`** — This is the base client class. It defaults to `http://localhost:11434` but accepts `base_url` as a parameter. This is the core abstraction.

2. **`llm_client.py`** — This is the main LLM client. It imports `OllamaClient` and creates instances with `base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434")`. It also has a vision client that uses `VISION_OLLAMA_HOST` or `OLLAMA_HOST`.

3. **`graph_ops.py`** — Imports `OllamaClient` directly.

4. **`note_creator.py`** — Imports `OllamaClient` directly.

5. **`self_improver.py`** — Imports `OllamaClient` directly.

6. **`services.py`** — Imports `OllamaClient` directly.

7. **`vault_indexer.py`** — Imports `OllamaClient` directly.

8. **`routers/llm.py`** — Creates `OllamaClient` instances directly with `OLLAMA_HOST` env var.

9. **`routers/system.py`** — Has `_ping_ollama()` function.

10. **`step_gate_runtime.py`** — The `llm_generate` preamble in the procedure runtime.

The key question is: does `llm_client.py` provide a `get_llm_client()` function that abstracts away the Ollama-specific details? And are all LLM calls going through this function, or are some bypassing it?

Let me check `llm_client.py` to understand the architecture, and then check each file that imports `OllamaClient` directly to see if they're bypassing the configured client.

The main concern is:
1. Files that create `OllamaClient` instances directly instead of using `get_llm_client()`
2. The `step_gate_runtime.py` preamble's `llm_generate` function — does it use `get_llm_client()` or create its own `OllamaClient`?

Let me check these files.OK, so `llm_client.py` has a `get_llm_client()` factory function that reads `LLM_BACKEND` from .env and returns either an `OllamaClient` or an `OpenAICompatibleClient`. This is the right abstraction.

Now the question is: which files bypass `get_llm_client()` and create `OllamaClient` directly?

From the search results, these files import `OllamaClient` directly:
1. `graph_ops.py` — line 34
2. `llm_client.py` — lines 424, 483 (these are inside `get_llm_client()` and `get_vision_client()`, so they're OK)
3. `note_creator.py` — line 6
4. `self_improver.py` — line 708
5. `services.py` — line 23
6. `vault_indexer.py` — line 11
7. `routers/llm.py` — lines 125, 177, 211-212, 278-279

Let me check each one to see if they're creating `OllamaClient` directly (bypassing `get_llm_client()`) or just importing it for type hints.

The key concern is:
- `services.py` — this is where the main service container is set up. It might create the LLM client.
- `step_gate_runtime.py` — the `llm_generate` preamble function. Let me check how it creates the LLM client.

Let me also check the `step_gate_runtime.py` preamble to see how `llm_generate` is defined.Now I have a clear picture. Let me categorize the files:

**OK — uses `get_llm_client()` (respects configured backend):**
- `routers/llm.py` ✓
- `step_gate_runtime.py` ✓
- `chat_handler.py` ✓
- `main.py` ✓
- `run_procedure.py` ✓

**PROBLEM — creates `OllamaClient` directly (bypasses configured backend):**
- `note_creator.py` — L29: `self.ollama_client = OllamaClient(session_logger=session_logger)`
- `self_improver.py` — L709: `client = OllamaClient()`
- `vault_indexer.py` — L68: `self.ollama_client = OllamaClient(...)`

**Need to check further:**
- `graph_ops.py` — imports OllamaClient but doesn't create instances directly. Might use it for type hints or receive it as a parameter.
- `services.py` — imports OllamaClient but doesn't create instances directly. Might use it for type hints or receive it as a parameter.

Now, there's an important distinction here:
- `vault_indexer.py` uses `OllamaClient` for **embeddings**, not for chat/synthesis. The `llm_client.py` docstring explicitly says "Embeddings are a SEPARATE concern and stay on OllamaClient (nomic-embed-text, ~270MB, light enough for a weak laptop)." So this might be intentional — embeddings always go through Ollama regardless of the chat backend.
- `note_creator.py` — let me check what it uses the OllamaClient for. If it's for chat/synthesis, it should use `get_llm_client()`. If it's for embeddings, it's OK.
- `self_improver.py` — L709 creates `OllamaClient()` with no arguments. This is likely for chat/synthesis (the self-improver generates text), so it should use `get_llm_client()`.

Let me check each one to understand what they use the client for.Interesting — `note_creator.py` creates an `OllamaClient` on line 29 but never actually uses `self.ollama_client` anywhere else in the file! It's a dead import/attribute. The note creator doesn't actually call the LLM directly — it just creates notes and maintains the vault.

Let me check `self_improver.py` and `vault_indexer.py` to see what they use the client for.OK, so here's the picture:

1. **`vault_indexer.py`** — Uses `OllamaClient` for **embeddings only** (`.embeddings()`, `.batch_embeddings()`). This is intentional — the `llm_client.py` docstring explicitly says "Embeddings are a SEPARATE concern and stay on OllamaClient (nomic-embed-text, ~270MB, light enough for a weak laptop)." So this is BY DESIGN and not a problem.

2. **`self_improver.py`** — L709: Creates `OllamaClient()` directly and calls `client.generate()` for the `self_reflect` function. This is a **chat/synthesis call** that bypasses `get_llm_client()`. This is a PROBLEM — if someone uses OpenAI-compatible API, `self_reflect` will try to connect to localhost:11434 and fail.

3. **`note_creator.py`** — Creates `OllamaClient` but never uses it. Dead code. Should be cleaned up.

4. **`graph_ops.py`** — Imports `OllamaClient` but doesn't create instances. Let me check if it uses it for type hints or receives it as a parameter.

5. **`services.py`** — Imports `OllamaClient` but doesn't create instances. Let me check what it does with it.

Let me also check:
- The `step_gate_runtime.py` preamble — how does `llm_generate` work? It uses `get_llm_client()`, but let me verify.
- The `textbook_read_page` and `textbook_ingest` tools — do they use the configured model?
- The `claim_verifier.py` — does it call the LLM directly?
- The `pattern_extractor.py` — does it call the LLM directly?
- The autonomous researcher — does it call the LLM directly?

Let me check the step_gate_runtime preamble first.Good — the `llm_generate` preamble in `step_gate_runtime.py` correctly uses `get_llm_client()`. That's fine.

Now let me check the other files that might call the LLM directly:
- `claim_verifier.py` — does it call the LLM?
- `pattern_extractor.py` — does it call the LLM?
- The autonomous researcher code
- `textbook_read_page` and `textbook_ingest` custom tools
- `graph_ops.py` — what does it use OllamaClient for?
- `services.py` — what does it use OllamaClient for?

Let me also check the `routers/llm.py` more carefully — it uses `get_llm_client()` but also has direct `OllamaClient` references.OK, now I have a clearer picture:

1. **`claim_verifier.py`** — Takes `llm_client` as a parameter and calls `self.llm_client.chat()`. This is OK — it receives the client from the caller. But I need to check WHO creates the `ClaimVerifier` and what client they pass in.

2. **`pattern_extractor.py`** — No LLM calls at all. Pure deterministic. Good.

3. **`graph_ops.py`** — Has `ollama_client` as a parameter and calls `self.ollama_client.generate()` on L228. This is a **chat/synthesis call** that goes through whatever client is passed in. I need to check who creates the `GraphOps` and what client they pass.

4. **`services.py`** — Imports `OllamaClient` for type hints. Has `ollama_client: OllamaClient` as a type annotation. Let me check how it creates the client.

Let me check `services.py` more carefully to see how it creates the `ollama_client` and whether it uses `get_llm_client()`.`services.py` doesn't create the `Services` instance — it just defines the dataclass. The actual creation happens in `main.py`. Let me check `main.py` to see how it creates the `ollama_client` and passes it to `Services`.OK, so `main.py` correctly uses `get_llm_client()` on line 304 to create the `ollama_client`, and then passes it to `Services` and all the modules that need it. This is the right pattern.

Now let me check the remaining problem areas:

1. **`self_improver.py`** — L709: Creates `OllamaClient()` directly. This is a PROBLEM.
2. **`note_creator.py`** — L29: Creates `OllamaClient()` but never uses it. Dead code.
3. **`graph_ops.py`** — L228: Calls `self.ollama_client.generate()`. But the client is passed in from `main.py`, which uses `get_llm_client()`. So this is OK as long as the caller passes the right client.
4. **`claim_verifier.py`** — Takes `llm_client` as a parameter. Need to check who creates it and what client they pass.

Let me check how `claim_verifier` is created in `main.py`:OK, so:

1. **`claim_verifier`** — L479: `ClaimVerifier(llm_client=ollama_client)` — receives the `ollama_client` from `main.py`, which is created via `get_llm_client()`. This is CORRECT.

2. **`self_improver`** — L395: `SelfImprover(session_logger=default_session_logger)` — doesn't receive the `ollama_client`! And inside `self_improver.py`, the `self_reflect` method creates `OllamaClient()` directly on L709. This is a PROBLEM.

Now let me also check:
- The autonomous researcher — does it call the LLM directly?
- The `textbook_read_page` and `textbook_ingest` custom tools — do they use the configured model?
- The `routers/llm.py` — it uses `get_llm_client()` but also has direct `OllamaClient` references. Let me check those.

Let me check the autonomous researcher and the custom tools.The autonomous researcher doesn't seem to directly create `OllamaClient` — it just references "Ollama" in comments. Let me check how it gets its LLM client.

The custom tools (`textbook_read_page.py` and `textbook_ingest.py`) are not found in the `custom_tools/` directory. They might be in a different location or might be built-in tools. Let me check.

Actually, looking at the tool list, `textbook_read_page` and `textbook_ingest` are custom tools that I created. They might be in `custom_tools/` with different filenames. Let me check.OK, so `textbook_read_page.py` takes `llm_client` as a parameter and uses it correctly. It even handles both `OpenAICompatibleClient` and `OllamaClient` for vision. This is fine.

`textbook_ingest.py` has no LLM calls — it's just downloading and parsing files.

Now let me also check the `routers/llm.py` more carefully — it had direct `OllamaClient` references:OK, so `routers/llm.py` uses `svc.ollama_client` (which comes from `get_llm_client()` in `main.py`) and `svc.vision_client` (which comes from `get_vision_client()`). The `OLLAMA_HOST` references in the router are just for display/config purposes (showing the user what the current config is), not for creating new clients. The `_rebuild_llm_client` function probably calls `get_llm_client()` again. This is fine.

Now let me also check the `_rebuild_llm_client` function:OK, `_rebuild_llm_client` correctly calls `get_llm_client()`. The `routers/llm.py` is fine.

Now let me also check the autonomous researcher to see how it gets its LLM client:The autonomous researcher uses `ResearchEngine` which does the actual research. Let me check if `ResearchEngine` creates its own `OllamaClient` or receives it as a parameter.`research_engine.py` has no LLM calls at all! The research engine is purely deterministic (web scraping, source gathering, etc.). The LLM synthesis happens elsewhere (in `main.py`'s chat loop or in the note creation process).

Let me now check how the autonomous researcher actually writes notes — does it use the `note_creator` which might call the LLM?OK, so the autonomous researcher uses `note_creator.create_note_from_research()` to write notes. Let me check if `create_note_from_research` calls the LLM:OK, so `create_note_from_research` doesn't call the LLM directly. It writes the note to disk and does cleanup. The LLM synthesis happens in the chat handler or research handler, which uses the `ollama_client` from `main.py` (which is created via `get_llm_client()`).

Now let me also check `graph_ops.py` line 228 more carefully — it calls `self.ollama_client.generate()`. Who creates the `GraphOpRegistry` and what client does it pass?OK, so `GraphOpRegistry` receives `ollama_client` from `main.py`, which is created via `get_llm_client()`. This is correct.

Now let me also check the `self_improver.py` more carefully. The `SelfImprover` is created on L395 of `main.py`:
```python
self_improver = SelfImprover(session_logger=default_session_logger)
```

It doesn't receive the `ollama_client`. And inside `self_improver.py`, the `self_reflect` method creates `OllamaClient()` directly. This is the main problem.

Let me also check if there are other methods in `self_improver.py` that create `OllamaClient` directly:OK, so `self_improver.py` has only one place where it creates `OllamaClient` directly — line 708-709, in the `self_reflect` method. This is the only LLM call in the file, and it bypasses `get_llm_client()`.

Now let me also check the `note_creator.py` — it creates `OllamaClient` on line 29 but never uses it. This is dead code that should be cleaned up.

Let me also check `graph_ops.py` line 228 more carefully to understand what it does:OK, so `graph_ops.py` uses `self.ollama_client.generate()` for entity extraction. But the client is passed in from `main.py` which uses `get_llm_client()`. So this is fine — it uses the configured client.

Now let me also check the `routers/system.py` `_ping_ollama` function:OK, so `_ping_ollama` in `routers/system.py` uses `svc.ollama_client.base_url` to ping the LLM backend. This is fine — it uses the configured client's URL, not a hardcoded Ollama URL. However, it does `requests.get(f"{base_url}/api/version")` which is an Ollama-specific endpoint. If the user is using an OpenAI-compatible API, this health check would fail even though the LLM is working. But this is a minor issue — it's just a health check, not a functional call.

Actually, wait — let me re-read this. It uses `getattr(svc.ollama_client, "base_url", None)` which gets the base_url from the configured client. For an OpenAI-compatible client, the base_url would be something like `https://api.openai.com`, and `/api/version` would return a 404 or something, not a 200. So the health check would report the LLM as down even when it's working.

This is a bug, but it's a minor one (health check reporting). The more important issue is the `self_improver.py` `self_reflect` method which creates `OllamaClient()` directly.

Let me now also check the Dream-Pass procedure's Step 3 health check. I added a socket check that connects to `OLLAMA_HOST` or `localhost:11434`. This is also an Ollama-specific check. If the user is using an OpenAI-compatible API, the health check would fail and the LLM synthesis would be skipped even though the LLM is available.

Actually, looking at the Dream-Pass Step 3 code more carefully, the health check I added checks for a socket connection to the Ollama port. But if the user is using an OpenAI-compatible API, the LLM is available at a different URL. The health check should use `get_llm_client().is_running()` instead of a raw socket check.

Wait, but the Dream-Pass procedure runs in a subprocess where `get_llm_client()` is available through the preamble. Let me check if `is_running()` is available on both `OllamaClient` and `OpenAICompatibleClient`.

Looking at the `llm_client.py` code:
- `OllamaClient` has `is_running()` method
- `OpenAICompatibleClient` — let me check if it has `is_running()`

Actually, looking at the `LLMClient` base class, it has `is_running()` as an abstract method. Both subclasses should implement it.

So the fix for the Dream-Pass Step 3 is to use `get_llm_client().is_running()` instead of the raw socket check.

Let me now summarize all the issues I've found:

## Issues Found

### Critical (bypasses configured model):
1. **`self_improver.py` L708-709** — `self_reflect()` creates `OllamaClient()` directly instead of using `get_llm_client()`. If user has `LLM_BACKEND=openai`, this will try to connect to localhost:11434 and fail.

2. **Dream-Pass Step 3 health check** — Uses raw socket check to `localhost:11434` instead of `get_llm_client().is_running()`. If user has `LLM_BACKEND=openai`, the health check will fail and LLM synthesis will be skipped even though the LLM is available.

### Minor (cosmetic/dead code):
3. **`note_creator.py` L29** — Creates `OllamaClient(session_logger=session_logger)` but never uses it. Dead code.

4. **`routers/system.py` `_ping_ollama`** — Uses `requests.get(f"{base_url}/api/version")` which is an Ollama-specific endpoint. For OpenAI-compatible backends, this will fail even though the LLM is working. Should use `svc.ollama_client.is_running()` instead.

### OK (uses configured model correctly):
- `main.py` — uses `get_llm_client()` ✓
- `step_gate_runtime.py` — uses `get_llm_client()` in preamble ✓
- `chat_handler.py` — uses `get_llm_client()` ✓
- `run_procedure.py` — uses `get_llm_client()` ✓
- `routers/llm.py` — uses `get_llm_client()` via `_rebuild_llm_client()` ✓
- `claim_verifier.py` — receives `llm_client` from `main.py` ✓
- `graph_ops.py` — receives `ollama_client` from `main.py` ✓
- `textbook_read_page.py` — receives `llm_client` as parameter ✓
- `vault_indexer.py` — uses `OllamaClient` for embeddings only (by design) ✓
- `autonomous_researcher.py` — no direct LLM calls ✓
- `research_engine.py` — no LLM calls ✓
- `pattern_extractor.py` — no LLM calls ✓

Let me fix the critical issues now.WARN status — uncommitted changes but no blocks. I can proceed with the edits. Let me fix all four issues.

1. Fix `self_improver.py` — replace `OllamaClient()` with `get_llm_client()`
2. Fix Dream-Pass Step 3 health check — replace socket check with `get_llm_client().is_running()`
3. Fix `note_creator.py` — remove dead `OllamaClient` creation
4. Fix `routers/system.py` — use `svc.ollama_client.is_running()` instead of raw HTTP ping

Let me start with `self_improver.py`.The issue is that `safe_write` replaces the entire file, not just a section. I need to provide the full file content. Let me read the full file and make the targeted change.

Actually, wait — `safe_write` writes the full file content. I need to read the entire file, make the change, and write it back. Let me do that.Now let me verify the import works:All 6 steps pass. And Step 3 actually wrote 2 new semantic notes this time:
- "Technical-Claims-Require-Explicit-Citation"
- "Execution-Loop-Dominance-Pattern"

The LLM service must be running now, and the `get_llm_client().is_running()` check correctly detected it.

Let me now do a final verification — make sure there are no remaining direct `OllamaClient()` constructor calls (excluding the factory in `llm_client.py` and the embeddings in `vault_indexer.py`):The encoding error is from the checkmark character. Let me fix that and re-run. But the important part is the first section — the remaining `OllamaClient()` constructor calls:

1. `llm_client.py:425` — This is inside `get_llm_client()`, the factory function. This is CORRECT — it creates an OllamaClient when `LLM_BACKEND=ollama`.
2. `llm_client.py:486` — This is inside `get_vision_client()`, the vision factory. This is CORRECT — it creates an OllamaClient for vision when `VISION_BACKEND=ollama`.
3. `vault_indexer.py:68` — This is for embeddings only. This is BY DESIGN — embeddings always use OllamaClient (nomic-embed-text).

All three remaining `OllamaClient()` calls are legitimate:
- Two are in the factory functions (`get_llm_client()` and `get_vision_client()`) which are the abstraction layer
- One is for embeddings (separate concern, by design)

The `self_improver.py` and `note_creator.py` direct `OllamaClient()` calls have been fixed. The `routers/system.py` health check has been fixed. The Dream-Pass Step 3 health check has been fixed.

Let me also verify the imports section without the unicode issue.

</details>