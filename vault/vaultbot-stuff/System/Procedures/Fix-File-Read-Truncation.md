---
type: procedure
status: verified
baseline: true
model_cartridge: small
created: 2026-08-10
description: "Audit and fix all layers in the chat pipeline that truncate file-read tool results (code_read, vault_read_note) before they reach the LLM. Ensures the model sees the WHOLE file in virtually all cases, with truncation only for extremely large files that would actually hurt the model."
when_to_use: when the model is getting truncated file contents, when file reads seem incomplete, when adjusting how much of a file the model can see, or when tuning the balance between full-file reads and context-window safety
falsifiable_if: a file under the read_result_cap threshold is still truncated before reaching the model, or any pipeline layer stubs a code_read/vault_read_note result without an explicit env-var opt-out
applies_to:
  - file-reading
  - context-management
  - truncation
  - tool-result-handling
allowed_tools:
  - code_read
  - grep
  - llm_generate
summary: Fix-File-Read-Truncation
tags:
  - procedure
  - procedures
  - truncation
  - file-reading
  - context-management
---

# Fix-File-Read-Truncation

## When to Run This

When the VaultBot is reading files but getting truncated content —
the model sees only part of a note or source file, or a `[...truncated...]`
marker appears in file-read results. This procedure audits every layer
in the chat pipeline that can cut file content and ensures each one
either passes the full content through or has a very high threshold.

## Why This Exists

File-read tool results pass through several pipeline layers, each of which
can truncate content before it reaches the model. This procedure audits
every layer and ensures each passes full content through or has a very high
threshold. The tradeoff: protecting full-file reads from truncation risks
overwhelming the context window, so the hard token cap remains the ultimate
backstop.

## Background: The Truncation Layers

File-read tool results (`code_read`, `vault_read_note`) pass through
several pipeline stages, each of which can truncate:

1. **Tool execution** (`execute_agent_tool` in `chat_handler.py`) —
   reads the file from disk. `vault_read_note` and `code_read` both
   return the full file content by default (no truncation here).

2. **`truncate_tool_result`** (called before appending to conversation) —
   caps the serialized result to `max_chars`. For read tools, this uses
   `VAULTBOT_READ_RESULT_CAP` (default 120K chars ≈ 30K tokens). This is
   the primary truncation point. Files under 120K chars pass through
   intact; only truly enormous files (500K+ chars) get cut.

3. **`_age_old_tool_results`** — stubs tool results older than N rounds.
   Read tools are EXEMPT via `VAULTBOT_TOOL_AGE_PROTECT_FILE_READS=1`.

4. **`_enforce_token_cap`** — the hard 60K-token ceiling. Phases 1 & 2
   stub old tool results to fit under the cap. Read tools are EXEMPT
   via `VAULTBOT_CAP_PROTECT_FILE_READS=1`. Phase 3 drops old middle
   messages (not tool results).

5. **`_sanitize_tool_history`** (glm-5.2:cloud via Ollama only) —
   converts tool messages to system messages. Read tools are EXEMPT
   from the 2000-char cap applied to other tool results.

## Steps

### Step 1: Audit the current read_result_cap threshold

```python
import os
from config import TUNABLES

cap = int(os.getenv("VAULTBOT_READ_RESULT_CAP", str(TUNABLES.read_result_cap)))
print(f"Read result cap: {cap} chars (~{cap//4} tokens)")
print(f"Files under {cap} chars pass through intact.")
print(f"Override via env var VAULTBOT_READ_RESULT_CAP (0 = use default).")
```

### Step 2: Verify all protection flags are enabled

```python
import os

flags = {
    "VAULTBOT_TOOL_AGE_PROTECT_FILE_READS": "protects read tools in _age_old_tool_results",
    "VAULTBOT_CAP_PROTECT_FILE_READS": "protects read tools in _enforce_token_cap phases 1&2",
}

for flag, desc in flags.items():
    val = os.getenv(flag, "1")  # default is "1" (enabled)
    status = "ENABLED" if val == "1" else "DISABLED"
    print(f"{flag}: {status} — {desc}")
```

### Step 3: Check the sanitize exemption for read tools

```python
# In _sanitize_tool_history, read tools bypass the 2000-char cap:
#   if tool_name not in ("code_read", "vault_read_note") \
#           and len(result_text) > 2000:
#       result_text = result_text[:2000] + "...[truncated]"
# This is hardcoded — verify it's still present.
import ast, pathlib

src = pathlib.Path("vaultbot/vaultbot_backend/chat_handler.py").read_text()
assert "code_read" in src and "vault_read_note" in src, "Read tool names missing from chat_handler.py"
assert "VAULTBOT_READ_RESULT_CAP" in src, "Read result cap env var missing"
print("Sanitize exemption and read cap are present in chat_handler.py")
```

### Step 4: If adjustment is needed, tune the cap

If files are still being truncated and the model needs more content,
raise the cap. If the context window is being overwhelmed by large
file reads, lower it. The hard token cap (`_enforce_token_cap`) is
the ultimate backstop — it will still stub other (non-read) tool
results and drop old messages to stay under 60K tokens.

```python
# To raise the cap to 200K chars (~50K tokens):
# Set env var: VAULTBOT_READ_RESULT_CAP=200000
# Or change the default in config.py: read_result_cap: int = 200000

# To disable read-tool protection in the token cap (NOT recommended —
# only if large file reads are causing OOM or timeout):
# Set env var: VAULTBOT_CAP_PROTECT_FILE_READS=0
# Set env var: VAULTBOT_TOOL_AGE_PROTECT_FILE_READS=0

print("See config.py TUNABLES.read_result_cap for the default.")
print("Override at runtime via VAULTBOT_READ_RESULT_CAP env var.")
```

## Related

- [[Smart-Code-Read]] — reads backend code with context awareness
- [[Smart-Note-Read]] — reads vault notes with context awareness
- [[Smart-Vault-Search]] — searches the vault with context awareness