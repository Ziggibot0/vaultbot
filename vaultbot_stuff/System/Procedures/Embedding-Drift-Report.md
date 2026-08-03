---
type: procedure
status: active
model_cartridge: small
created: 2026-08-03
description: "Detect when the vault's embedding index has drifted from the actual file state — files added/modified/deleted since last indexing, stale embeddings, or index size mismatch. Fully deterministic: compares the embedding index metadata against the actual vault file listing. No LLM reasoning needed — the small model only formats the report."
when_to_use: "when retrieval quality seems degraded, when the vault has had many changes without a reindex, when asked 'is the index stale?', or as part of a Dream-Pass health check"
falsifiable_if: "the report claims files are missing from the index when they're present, or claims the index is current when files have been modified since last embedding"
applies_to:
  - embedding-index
  - retrieval-quality
  - system-health
  - vault-maintenance
allowed_tools:
  - vault_list
  - code_read
---

# Embedding-Drift-Report

## When to Run This

Run when retrieval quality seems off, after bulk imports or large vault
changes, or as part of a Dream-Pass. The embedding index can drift when
files are added, modified, or deleted without triggering a reindex —
especially if the file watcher missed events or the backend was restarted
unexpectedly.

This procedure is **fully deterministic**. The small model only formats
the structured report into readable prose.

## Steps

### Step 1: Get the actual vault file listing

1. ```python
import os
import json

vault = str(vault_path)
md_files = []
for root, dirs, files in os.walk(vault):
    # Skip trash and backend internals
    if "vaultbot_backend" in root and "trash" in root:
        continue
    for f in files:
        if f.endswith(".md"):
            rel = os.path.relpath(os.path.join(root, f), vault)
            md_files.append(rel)

print(f"Vault has {len(md_files)} .md files on disk")
```

### Step 2: Read the embedding index metadata and compare

2. ```python
import os
import json
import time

index_path = str(Path(vault_path) / "vaultbot_backend" / "vault_index.json")

if not os.path.exists(index_path):
    print("WARNING: No embedding index file found at", index_path)
    print("The index may be stored in a different location or not yet built.")
    report = {"error": "no_index_file", "vault_md_count": len(md_files)}
else:
    with open(index_path, "r", encoding="utf-8") as f:
        index_data = json.load(f)

    # The index stores metadata about each embedded file
    indexed_files = set()
    stale_files = []
    index_meta = index_data.get("metadata", index_data)

    if isinstance(index_meta, dict):
        for filepath, meta in index_meta.items():
            indexed_files.add(filepath)
            # Check if the file still exists
            full_path = os.path.join(vault, filepath)
            if not os.path.exists(full_path):
                stale_files.append({"file": filepath, "issue": "deleted_but_still_indexed"})
    elif isinstance(index_meta, list):
        for entry in index_meta:
            fp = entry.get("file_path", entry.get("path", ""))
            if fp:
                indexed_files.add(fp)
                full_path = os.path.join(vault, fp)
                if not os.path.exists(full_path):
                    stale_files.append({"file": fp, "issue": "deleted_but_still_indexed"})

    # Files on disk but not in index
    vault_set = set(md_files)
    missing_from_index = vault_set - indexed_files
    in_index_not_disk = indexed_files - vault_set

    report = {
        "vault_md_count": len(md_files),
        "indexed_count": len(indexed_files),
        "missing_from_index": sorted(list(missing_from_index))[:50],
        "missing_from_index_count": len(missing_from_index),
        "deleted_but_indexed": sorted(list(in_index_not_disk))[:50],
        "deleted_but_indexed_count": len(in_index_not_disk),
        "drift_ratio": round(len(missing_from_index) / max(len(md_files), 1), 3),
        "status": "healthy" if len(missing_from_index) == 0 and len(in_index_not_disk) == 0 else "drifted"
    }

print(json.dumps(report, indent=2))
```

### Step 3: Check index file age and size

3. ```python
import os
import time

index_path = str(Path(vault_path) / "vaultbot_backend" / "vault_index.json")

if os.path.exists(index_path):
    stat = os.stat(index_path)
    age_hours = round((time.time() - stat.st_mtime) / 3600, 1)
    size_mb = round(stat.st_size / (1024 * 1024), 2)

    age_report = {
        "index_age_hours": age_hours,
        "index_size_mb": size_mb,
        "last_modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        "recommendation": "reindex" if age_hours > 24 and report.get("drift_ratio", 0) > 0.05 else "ok"
    }
    print(json.dumps(age_report, indent=2))
else:
    print("No index file to check age/size")
```

### Step 4: Format the report

4. [llm: Format the embedding drift report into a concise summary for the operator. Include: (1) whether the index is healthy or drifted, (2) how many files are missing from the index, (3) how many deleted files are still indexed, (4) the drift ratio, (5) index age, and (6) a recommendation — "reindex" if drift > 5% or index is > 24 hours old with drift, "healthy" otherwise. Keep it to 5-10 lines max. No reasoning, just the formatted report.]


## Related

- [[Vault-Health-Check]] — checks vault graph health; Embedding-Drift-Report complements it by checking the embedding index, because a healthy graph with a stale index means retrieval quality degrades silently
- [[Procedure-Expansion-Proposal]] — proposed as Tier 3 because drift detection is purely deterministic file comparison, therefore the small model only formats the report
- [[Dream-Pass]] — Embedding-Drift-Report can run as a Dream-Pass step to catch index staleness before it affects retrieval quality
- [[Tiny-LLM-Use-Cases-Mapping-to-VaultBot-Procedure-Cartridge]] — format conversion of structured data into prose is a proven small-model task, which means the LLM step here is safely small-cartridge
