---
type: procedure
status: experimental
baseline: true
created: 2026-08-19
description: "Generate a structured map of VaultBot's own backend source: every module with its docstring, top-level functions/classes, and imports. Writes vaultbot-stuff/Knowledge/Architecture/Codebase-Map.md. Deterministic AST walk — no LLM, no embeddings. Run this when the code changes, then read the map instantly via the codebase_map tool."
when_to_use: "When you need to understand VaultBot's own code before editing it, when the codebase map is missing or stale, or when asked 'what does your code look like'."
falsifiable_if: "The map misses a module, mislabels a function/class, or the generated note is unreadable by the codebase_map tool."
applies_to:
  - self-knowledge
  - code-comprehension
  - self-modification
  - codebase-map
allowed_tools:
  - code_read
summary: Codebase-Map
tags:
  - procedure
  - procedures
  - self-knowledge
  - codebase-map
---

# Codebase-Map

## Purpose

Generate a structured index of VaultBot's own backend source so the agent
can "understand any part of its own code in an instant." The map is a single
note that the `codebase_map` tool reads back in one call — no per-file
`code_read` round-trips.

## Why This Exists

Understanding any part of the backend required per-file `code_read` round-trips, which is slow and scattered. This procedure generates a single map note that the `codebase_map` tool reads back in one call. The key tradeoff is that the map is a deterministic AST walk — no LLM, no embeddings — so it is cheap but must be regenerated when the code changes.

## When to Run

- Before editing backend code (to locate the right module/function)
- When the map is missing or stale (code changed since last generation)
- When asked "what does your code look like"

## Output

Writes `vaultbot-stuff/Knowledge/Architecture/Codebase-Map.md` with one `## <module>`
section per backend `.py` file, each containing the module docstring (first
line), top-level functions/classes with line numbers, and imports.

## Steps

### Step 1: Walk the backend and write the codebase map

This step walks every `.py` file in the backend directory, extracts each
module's docstring, top-level functions/classes, and imports using Python's
`ast` module, and writes the result to `Codebase-Map.md`. It's fully
deterministic — no LLM calls, no embeddings.

```python
import ast
import json
import os
from pathlib import Path

backend_dir = Path(FRAMEWORK_ROOT) / "vaultbot_backend"
out_dir = Path(vault_path) / "vaultbot-stuff" / "Knowledge" / "Architecture"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "Codebase-Map.md"

# Directories to skip when walking the backend.
SKIP_DIRS = {
    "__pycache__",
    "tests",
    "sessions",
    "checkpoints",
    "trash",
    "vaultbot_index",
    "identity",
    "routers",
    "session_state",
    "custom_tools",
    "scripts",
}

sections = []
module_count = 0

for py in sorted(backend_dir.glob("*.py")):
    if py.name.startswith("_"):
        continue
    try:
        src = py.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue

    # Module docstring (first line only).
    doc = ast.get_docstring(tree) or ""
    doc_line = doc.strip().splitlines()[0] if doc.strip() else "(no docstring)"

    # Top-level imports.
    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                imports.append(f"{mod}.{a.name}" if mod else a.name)

    # Top-level functions and classes with line numbers.
    defs = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.append((node.lineno, "def", node.name))
        elif isinstance(node, ast.ClassDef):
            defs.append((node.lineno, "class", node.name))

    defs.sort()

    lines = [f"## {py.stem}", ""]
    lines.append(f"**Purpose:** {doc_line}")
    lines.append("")
    if imports:
        lines.append("**Imports:** " + ", ".join(imports[:20]))
        lines.append("")
    if defs:
        lines.append("**Top-level definitions:**")
        for lineno, kind, name in defs:
            lines.append(f"- `{kind} {name}` (line {lineno})")
    else:
        lines.append("**Top-level definitions:** (none)")
    lines.append("")

    sections.append("\n".join(lines))
    module_count += 1

header = (
    "# Codebase-Map\n\n"
    "Auto-generated index of VaultBot's backend source. Regenerate with the "
    "Codebase-Map procedure when the code changes. Read it instantly with the "
    "`codebase_map` tool.\n\n"
    f"**Modules:** {module_count}\n\n"
)

out_path.write_text(header + "\n".join(sections), encoding="utf-8")

result = json.dumps({
    "status": "ok",
    "modules": module_count,
    "output_path": str(out_path),
})
print(result)
```

## Related

- [[Analyze-Function-Flow]] — traces a function's call graph through the map
- [[Code-Pattern-Extract]] — searches the backend for patterns
- [[Code-Structure-Check]] — checks a file's conventions
