---
type: procedure
status: experimental
baseline: true
model_cartridge: small
created: 2026-08-02
description: "Remove junk files from the vault: pytest cache, .bak/.orig/.tmp files, empty .md files, and trash remnants. Takes graph analyzer output (with isolated_nodes) as optional input via args."
when_to_use: As part of a Dream-Pass cycle, or independently to clean up junk files.
applies_to:
  - vault
  - maintenance
  - cleanup
allowed_tools:
  - vault_delete
falsifiable_if: it deletes sacred journals, LOCKED notes, or non-junk files
success_count: 0
failure_count: 0
success_rate: 0.0
summary: Dream-Prune
tags:
  - procedure
  - procedures
---

# Dream-Prune

Scans for and removes pytest cache files, duplicate/backup files, corrupted filenames, and trash remnants. Always backs up before deleting (vault_delete does this automatically). Never deletes sacred journals, LOCKED notes, or identity files.

## Step 1: Find and remove junk files

1. ```python
import json, os

vault_path = os.environ.get("VAULT_PATH", ".")
_IGNORED_DIRS = {'.obsidian', '.git', 'vaultbot_backend', 'node_modules', '__pycache__', '.venv', 'trash'}

# Get isolated nodes from args (passed by orchestrator) or from prior_results
graph_data = args.get("graph_data", "")
if not graph_data and len(prior_results) > 0:
    graph_data = prior_results[0]
try:
    _step1_data = json.loads(graph_data) if isinstance(graph_data, str) else graph_data
except:
    _step1_data = {}
isolated = _step1_data.get("isolated_nodes", [])

junk_patterns = [".pytest_cache", "baseline/", ".bak", ".orig", ".tmp",
               "_restart.bat", "_restart.sh", "trash/"]
junk_files = [f for f in isolated if any(p in f for p in junk_patterns)]
# Also flag empty .md files (0 bytes) in isolated nodes
for f in isolated:
    full_path = os.path.join(vault_path, f) if not os.path.isabs(f) else f
    if f.endswith(".md") and os.path.exists(full_path):
        if os.path.getsize(full_path) == 0:
            junk_files.append(f)
# Also scan all vault files for junk
for root, dirs, files in os.walk(vault_path):
    dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
    for f in files:
        if f.endswith(".md") and any(p in f for p in junk_patterns):
            junk_files.append(os.path.join(root, f))

deleted = []
for f in junk_files:
    try:
        vault_delete(f)
        deleted.append(f)
    except:
        pass

result = json.dumps({"junk_deleted": deleted})
```