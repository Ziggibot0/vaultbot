---
type: procedure
status: experimental
baseline: true
created: 2026-08-22
description: "Walk all procedure .md files, extract the custom_tools and run_procedure references, and map each procedure to the backend source files it depends on. Writes vaultbot-stuff/Knowledge/Architecture/Procedure-Code-Map.md."
when_to_use: "When you need to understand which backend modules a procedure touches, or before editing a backend module to predict which procedures might break. Called by Predict-Change-Impact and Know-Thyself."
falsifiable_if: The map misses a real tool reference, lists a phantom reference, or the output note is unreadable.
applies_to:
  - self-knowledge
  - code-comprehension
  - procedure-map
allowed_tools:
  - code_run
  - code_read
summary: Maps procedure .md files to the backend code they use.
tags:
  - procedure
  - self-knowledge
  - procedure-map
---

# Map-Procedure-Code

## Purpose

Generate a cross-reference map between VaultBot's procedure files and the
backend source code they depend on. For each procedure, list which
`custom_tools` it imports and which other procedures it calls via
`run_procedure`. This enables change-impact prediction: if you edit a backend
module, you can quickly find which procedures might be affected.

## Why This Exists

`Build-Dependency-Graph` maps Python-to-Python dependencies, but procedures
are Markdown files that reference tools by name. This procedure traces the
bridge between the two worlds: procedure → tool name → Python module.

## Output

Writes `vaultbot-stuff/Knowledge/Architecture/Procedure-Code-Map.md` with:
- One `## <procedure>` section per procedure file
- **Tools used:** custom_tools referenced in the procedure
- **Sub-procedures:** other procedures called via `run_procedure`
- **Backend modules:** resolved Python files for each tool

## Steps

### Step 1: Walk procedures and map tools to backend code

This step walks every procedure `.md` file, extracts tool references and
sub-procedure calls, then resolves each tool to its backend Python module.

```python
import ast
import json
import os
import re
from pathlib import Path

vault_root = Path(vault_path)
proc_dir = vault_root / "myvault" / "vaultbot-stuff" / "System" / "Procedures"
backend_dir = Path(FRAMEWORK_ROOT) / "vaultbot_backend"
custom_tools_dir = backend_dir / "custom_tools"
out_dir = vault_root / "vaultbot-stuff" / "Knowledge" / "Architecture"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "Procedure-Code-Map.md"

# Patterns to extract
TOOL_IMPORT_RE = re.compile(r"from custom_tools\.(\w+) import run")
RUN_PROCEDURE_RE = re.compile(r"run_procedure\s*\(\s*['\"]([^'\"]+)['\"]")
ALLOWED_TOOLS_RE = re.compile(r"^\s*-\s+(\S+)", re.MULTILINE)

# Known tool -> module mapping
TOOL_TO_MODULE = {
    "code_read": "code_read.py",
    "code_run": "code_run.py",
    "vault_search": "vault_search.py",
    "vault_list": "vault_list.py",
    "vault_gaps": "vault_gaps.py",
    "vault_safe_write": "vault_safe_write.py",
    "vault_append": "vault_append.py",
    "vault_lint": "vault_lint.py",
    "vault_graph_analyzer": "vault_graph_analyzer.py",
    "vault_delete": "vault_delete.py",
    "llm_generate": "llm_generate.py",
    "run_procedure": "procedure_step_executor.py",
    "machine_spec": "machine_spec.py",
    "ollama_model_search": "ollama_model_search.py",
    "vaultbot_status": "vaultbot_status.py",
    "vault_research": "vault_research.py",
    "web_read_source": "web_read_source.py",
    "github_issues": "github_issues.py",
    "submit_contribution": "submit_contribution.py",
    "review_contributions": "review_contributions.py",
    "torture_test": "torture_test.py",
    "pr_feedback": "pr_feedback.py",
    "backend_restart": "backend_restart.py",
    "plugin_reload": "plugin_reload.py",
    "preflight_safety_check": "preflight_safety_check.py",
    "vaultbot_sync": "vaultbot_sync.py",
    "textbook_ingest": "textbook_ingest.py",
    "textbook_read_page": "textbook_read_page.py",
    "vault_cluster_analyzer": "vault_cluster_analyzer.py",
}

# Map procedure name -> {tools, sub_procedures, backend_modules}
procedure_map = {}

for md in sorted(proc_dir.glob("*.md")):
    proc_name = md.stem
    content = md.read_text(encoding="utf-8", errors="replace")

    # Extract allowed_tools from YAML frontmatter
    allowed_match = re.search(r"allowed_tools:\s*\n((?:\s+-\s+\S+\n?)+)", content)
    allowed_tools = []
    if allowed_match:
        allowed_tools = ALLOWED_TOOLS_RE.findall(allowed_match.group(1))

    # Extract custom_tools imports from code blocks
    tool_imports = list(set(TOOL_IMPORT_RE.findall(content)))

    # Extract run_procedure calls from code blocks
    sub_procs = list(set(RUN_PROCEDURE_RE.findall(content)))

    # Resolve tools to backend modules
    backend_modules = set()
    for tool in set(allowed_tools + tool_imports):
        if tool in TOOL_TO_MODULE:
            backend_modules.add(TOOL_TO_MODULE[tool])
        # Also check if it's a direct custom_tools import
        module_file = custom_tools_dir / f"{tool}.py"
        if module_file.exists():
            backend_modules.add(f"{tool}.py")

    # For direct imports like "from custom_tools.X import run"
    for tool_name in tool_imports:
        module_file = custom_tools_dir / f"{tool_name}.py"
        if module_file.exists():
            backend_modules.add(f"{tool_name}.py")

    # Check if procedure uses subprocess (references git, python, etc.)
    uses_subprocess = "subprocess" in content
    uses_ast = "import ast" in content

    procedure_map[proc_name] = {
        "allowed_tools": sorted(set(allowed_tools)),
        "tool_imports": sorted(tool_imports),
        "sub_procedures": sorted(sub_procs),
        "backend_modules": sorted(backend_modules),
        "uses_subprocess": uses_subprocess,
        "uses_ast": uses_ast,
    }

# Write the map note
sections = []
for proc_name, info in sorted(procedure_map.items()):
    lines = [f"## {proc_name}", ""]

    if info["allowed_tools"]:
        lines.append(f"**Allowed tools:** {', '.join(f'`{t}`' for t in info['allowed_tools'])}")
    else:
        lines.append("**Allowed tools:** (none declared)")

    if info["tool_imports"]:
        lines.append(f"**Direct imports:** {', '.join(f'`custom_tools.{t}`' for t in info['tool_imports'])}")

    if info["sub_procedures"]:
        lines.append(f"**Sub-procedures:** {', '.join(f'`[[{p}]]`' for p in info['sub_procedures'])}")

    if info["backend_modules"]:
        lines.append(f"**Backend modules:** {', '.join(f'`{m}`' for m in info['backend_modules'])}")

    extras = []
    if info["uses_subprocess"]:
        extras.append("subprocess")
    if info["uses_ast"]:
        extras.append("ast")
    if extras:
        lines.append(f"**Also uses:** {', '.join(extras)}")

    lines.append("")
    sections.append("\n".join(lines))

# Reverse index: backend module -> procedures that use it
reverse_map = {}
for proc_name, info in procedure_map.items():
    for mod in info["backend_modules"]:
        reverse_map.setdefault(mod, []).append(proc_name)

reverse_sections = [f"## {mod}", ""]
for mod in sorted(reverse_map):
    procs = reverse_map[mod]
    reverse_sections.append(f"- `{mod}` ← {', '.join(f'`{p}`' for p in sorted(procs))}")
reverse_sections.append("")

total_procedures = len(procedure_map)
total_tools = sum(len(info["allowed_tools"]) for info in procedure_map.values())

header = (
    "# Procedure-Code-Map\n\n"
    "Auto-generated cross-reference between VaultBot's procedure files and the backend source "
    "code they depend on. Regenerate with the Map-Procedure-Code procedure when procedures change.\n\n"
    f"**Procedures:** {total_procedures}  \n"
    f"**Total tool references:** {total_tools}\n\n"
    "---\n\n"
)

header2 = "\n## Reverse Index: Backend Module → Procedures\n\n"

out_path.write_text(
    header + "\n".join(sections) + header2 + "\n".join(reverse_sections),
    encoding="utf-8",
)

# Write JSON sidecar so Predict-Change-Impact doesn't have to regex-parse
# the markdown. This is the machine-readable contract between the two.
json_path = out_dir / "Procedure-Code-Map.json"
proc_json_data = {
    "procedure_map": {
        proc: {
            "allowed_tools": info["allowed_tools"],
            "tool_imports": info["tool_imports"],
            "sub_procedures": info["sub_procedures"],
            "backend_modules": info["backend_modules"],
        }
        for proc, info in procedure_map.items()
    },
    "module_to_procedures": {
        mod: sorted(procs) for mod, procs in reverse_map.items()
    },
    "total_procedures": total_procedures,
    "total_tool_refs": total_tools,
}
json_path.write_text(json.dumps(proc_json_data, indent=1), encoding="utf-8")

result = json.dumps({
    "status": "ok",
    "procedures": total_procedures,
    "total_tool_refs": total_tools,
    "output_path": str(out_path),
    "json_path": str(json_path),
})
print(result)
```

## Related

- [[Build-Dependency-Graph]] — Python-to-Python import dependencies
- [[Predict-Change-Impact]] — uses this map to predict blast radius
- [[Codebase-Map]] — module-level function/class index