---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-22
description: "Walk all backend .py files with ast, extract import dependencies, and build a forward/reverse dependency graph. Writes vaultbot/Knowledge/Architecture/Dependency-Graph.md. Deterministic AST walk — no LLM, no embeddings."
when_to_use: "When you need to understand which modules depend on which, or before editing code to predict blast radius. Called by Predict-Change-Impact and by Know-Thyself."
falsifiable_if: The graph misses a real import, lists a phantom import, or the output note is unreadable.
applies_to:
  - self-knowledge
  - code-comprehension
  - dependency-graph
allowed_tools:
  - code_run
  - code_read
summary: Builds a forward/reverse Python dependency graph of the backend.
tags:
  - procedure
  - self-knowledge
  - dependency-graph
---

# Build-Dependency-Graph

## Purpose

Generate a complete import dependency graph of VaultBot's backend source.
For every module, list what it imports and what imports it. This enables
change-impact prediction ("if I edit X, what else might break?") and helps
the agent understand its own code structure.

## Why This Exists

The `Codebase-Map` procedure indexes functions/classes but doesn't trace
dependencies. When editing a module, the agent needs to know which other
modules will be affected. This procedure fills that gap with a deterministic
AST walk — no LLM, no embeddings.

## Output

Writes `vaultbot/Knowledge/Architecture/Dependency-Graph.md` with:
- One `## <module>` section per backend `.py` file
- **Imports:** what this module imports from other backend modules
- **Imported-by:** what other backend modules import this one
- **External deps:** non-backend imports (stdlib, pip packages)

## Steps

### Step 1: Walk the backend and build the dependency graph

This step walks every `.py` file in the backend directory, extracts imports
using Python's `ast` module, resolves them to backend module names, and
builds both forward and reverse dependency maps.

```python
import ast
import json
import os
from pathlib import Path

backend_dir = Path(vault_path) / "vaultbot" / "vaultbot_backend"
out_dir = Path(vault_path) / "vaultbot" / "Knowledge" / "Architecture"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "Dependency-Graph.md"

SKIP_DIRS = {
    "__pycache__", "tests", "sessions", "checkpoints", "trash",
    "vaultbot_index", "identity", "routers", "session_state",
}

# Module stem -> set of module stems it imports (forward deps)
forward_deps = {}  # module -> {imported_modules}
# Module stem -> set of module stems that import it (reverse deps)
reverse_deps = {}  # module -> {importing_modules}
# Module stem -> list of external (non-backend) imports
external_deps = {}  # module -> [external_packages]

# First pass: collect all backend module stems
backend_modules = set()
for py in backend_dir.glob("*.py"):
    if py.name.startswith("_"):
        continue
    backend_modules.add(py.stem)

# Initialize
for mod in backend_modules:
    forward_deps.setdefault(mod, set())
    reverse_deps.setdefault(mod, set())
    external_deps.setdefault(mod, [])

# Second pass: walk each module, extract imports
for py in sorted(backend_dir.glob("*.py")):
    if py.name.startswith("_"):
        continue
    stem = py.stem
    try:
        src = py.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in backend_modules:
                    forward_deps[stem].add(top)
                    reverse_deps.setdefault(top, set()).add(stem)
                elif top not in ("__future__",):
                    external_deps[stem].append(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            top = node.module.split(".")[0]
            if top in backend_modules:
                forward_deps[stem].add(top)
                reverse_deps.setdefault(top, set()).add(stem)
            elif top not in ("__future__",):
                # Only add unique external deps
                if top not in external_deps.get(stem, []):
                    external_deps.setdefault(stem, []).append(top)

# Write the graph note
sections = []
for mod in sorted(backend_modules):
    fwd = sorted(forward_deps.get(mod, set()))
    rev = sorted(reverse_deps.get(mod, set()))
    ext = sorted(set(external_deps.get(mod, [])))

    lines = [f"## {mod}", ""]
    if fwd:
        lines.append(f"**Imports:** {', '.join(f'`{m}`' for m in fwd)}")
    else:
        lines.append("**Imports:** (none)")
    if rev:
        lines.append(f"**Imported-by:** {', '.join(f'`{m}`' for m in rev)}")
    else:
        lines.append("**Imported-by:** (nothing — leaf module)")
    if ext:
        lines.append(f"**External deps:** {', '.join(f'`{m}`' for m in ext[:15])}")
        if len(ext) > 15:
            lines.append(f"  ... and {len(ext) - 15} more")
    lines.append("")
    sections.append("\n".join(lines))

# Stats
total_modules = len(backend_modules)
total_edges = sum(len(v) for v in forward_deps.values())
hub_modules = sorted(
    backend_modules,
    key=lambda m: len(reverse_deps.get(m, set())),
    reverse=True,
)[:5]

header = (
    "# Dependency-Graph\n\n"
    "Auto-generated forward/reverse dependency graph of VaultBot's backend source. "
    "Regenerate with the Build-Dependency-Graph procedure when the code changes.\n\n"
    f"**Modules:** {total_modules}  \n"
    f"**Import edges:** {total_edges}  \n"
    f"**Most-imported modules:** {', '.join(f'`{m}` ({len(reverse_deps.get(m, set()))})' for m in hub_modules)}\n\n"
    "---\n\n"
)

out_path.write_text(header + "\n".join(sections), encoding="utf-8")

# Write JSON sidecar so Predict-Change-Impact doesn't have to regex-parse
# the markdown. This is the machine-readable contract between the two.
json_path = out_dir / "Dependency-Graph.json"
graph_data = {
    "forward_deps": {k: sorted(v) for k, v in forward_deps.items()},
    "reverse_deps": {k: sorted(v) for k, v in reverse_deps.items()},
    "external_deps": {k: sorted(set(v)) for k, v in external_deps.items()},
    "module_count": total_modules,
    "edge_count": total_edges,
}
json_path.write_text(json.dumps(graph_data, indent=1), encoding="utf-8")

result = json.dumps({
    "status": "ok",
    "modules": total_modules,
    "import_edges": total_edges,
    "most_imported": [(m, len(reverse_deps.get(m, set()))) for m in hub_modules],
    "output_path": str(out_path),
    "json_path": str(json_path),
})
print(result)
```

## Related

- [[Codebase-Map]] — module-level function/class index (complements this graph)
- [[Predict-Change-Impact]] — uses this graph to predict blast radius
- [[Map-Procedure-Code]] — maps procedure files to the backend code they use