---
type: procedure
status: active
baseline: true
model_cartridge: big
created: 2026-08-15
description: "Choose the correct write tool based on file type and edit scope. Prevents the safe_write-on-markdown bug that destroyed IDENTITY.md in session 15e346b7, and the thought-loop spiral that followed."
when_to_use: "When you need to write or edit any file in the vault. Use this BEFORE calling any write tool to pick the right one."
allowed_tools:
  - md_safe_replace
  - safe_write
  - vault_safe_write
  - code_run
  - edit_lines
  - thought
summary: |
  Decision tree for choosing the correct write tool. The wrong tool can
  silently destroy files (safe_write on .md writes 0 bytes) or trigger
  syntax errors (safe_write on .md with em-dashes). This procedure ensures
  the model picks the right tool the first time.
tags:
  - procedure
  - write-safety
  - tool-selection
falsifiable_if: "the procedure produces incorrect output or fails to complete its stated task"
---

# Choose-Write-Tool

## Purpose

Picking the wrong write tool has caused file destruction (session 15e346b7:
safe_write on IDENTITY.md wrote 0 bytes because the model passed old_str/new_str
instead of content, and safe_write silently ast.parsed the empty string and
wrote it). This procedure is a decision tree that ensures the correct tool is
chosen based on file type and edit scope, preventing that class of failure.

## Decision Tree

```
Is the file .py?
  YES → Is it a full rewrite or new file?
    YES → safe_write (file_path, content)
    NO  → safe_replace (file_path, old_str, new_str) or edit_lines (file_path, start_line, end_line, new_str)
Is the file .md?
  YES → Is it a full rewrite or new file?
    YES → vault_safe_write (file_path, content)
    NO  → md_safe_replace (file_path, old_str, new_str) or edit_lines (file_path, start_line, end_line, new_str)
Is the file .js/.mjs/.cjs?
  YES → js_safe_write (file_path, content)
Is it any other file type?
  YES → code_run with Python file I/O (open(path, 'w', encoding='utf-8'))
```

## Critical Rules

1. **safe_write is for PYTHON (.py) files ONLY.** It runs `ast.parse()` on the
   content. On a .md file, this either succeeds on empty content (writing 0
   bytes — destroying the file) or fails with a SyntaxError on non-ASCII
   characters like em-dashes.

2. **safe_write requires a `content` parameter**, NOT `old_str`/`new_str`. If
   you pass old_str/new_str (which are md_safe_replace parameters), the
   content will be empty and the file will be overwritten with 0 bytes.

3. **md_safe_replace is for MARKDOWN (.md) files ONLY.** It does exact-string
   matching and atomic writes with backup.

4. **vault_safe_write is for full-file writes of markdown notes** in the vault.
   It requires the FULL file content.

5. **edit_lines works on both .py and .md files** but requires frontmatter for
   .md files. For .md files without frontmatter (like IDENTITY.md), use
   md_safe_replace or vault_safe_write instead.

## Steps

### Step 1: Identify the file type and edit scope

[llm: Determine the file extension (.py, .md, .js, other) and whether you need a full rewrite or a targeted edit. Check the decision tree above to pick the correct tool.]

### Step 2: Verify the parameters match the tool

[validate: the tool name and its required parameters match according to the decision tree. safe_write needs file_path + content. md_safe_replace needs file_path + old_str + new_str. vault_safe_write needs file_path + content.]

### Step 3: Call the correct write tool

```python
# Example: targeted edit to a .md file
result = md_safe_replace(
    file_path="vaultbot/vaultbot_backend/identity/IDENTITY.md",
    old_str="# SECTION TO REPLACE\n\nold content",
    new_str="# SECTION TO REPLACE\n\nnew content",
)
print(result)
```

### Step 4: Verify the write succeeded

[llm: Check the tool result. If status is "rejected" or "error", read the error message and the hint. Do NOT immediately retry with the same tool — the error message tells you which tool to use instead. Switch tools based on the hint, then retry.]

### Step 5: If a write tool fails, do NOT loop on thought

[condition: if the write was rejected or failed, branch to step 6]

### Step 6: Diagnose and switch tools, do not spiral

[llm: If a write failed, read the error message carefully. It will tell you why it failed and which tool to use instead. Switch to the correct tool and retry ONCE. If it fails again, explain the problem to the user in a text response and end the turn. Do NOT call the thought tool repeatedly — that is a thinking loop. The thought-loop detector will stop the turn after 5 consecutive thought-only rounds.]