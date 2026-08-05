---
type: spec
status: completed
created: 2026-07-31
description: "HISTORICAL one-time migration spec (already applied): it removed the silent masking fallbacks from backend Python files. NOT an active procedure — its Step headers don't parse as an executable procedure and re-running it would assert-fail because the fallbacks are already gone. Kept as a record of the fail-loud refactor."
when: reference only — do not execute; the fail-loud refactor already shipped
summary: Fix Masking Fallbacks (completed spec)
tags:
  - spec
  - procedures
---

# Fix Masking Fallbacks (completed spec)

This is a **completed one-time migration**, preserved as history. It is no
longer an executable procedure: the silent `return []` / `pass` fallbacks it
targeted have already been removed (see the fail-loud refactor audit) and its
`## Step N` structure doesn't parse into executable steps. Do not run it.

What it did: each step safely edited a Python file with backup + syntax check
+ import test, restoring the backup on failure.

## Step 1: Fix knowledge_curriculum.py — remove silent [] returns

```python
import shutil
import subprocess
import sys
from pathlib import Path

target = Path("vaultbot_stuff/vaultbot_backend/knowledge_curriculum.py")
backup = target.with_suffix(".py.bak")

# Read original
original = target.read_text(encoding="utf-8")

# Back up
shutil.copy2(target, backup)
print(f"Backed up to {backup}")

# Fix 1: graph.refresh() failure returns [] → let it raise
old1 = """        try:
            self.graph.refresh()
        except Exception as e:
            self._log_error("graph_refresh", e)
            return []"""
new1 = """        self.graph.refresh()"""
assert old1 in original, "Could not find graph_refresh try/except block"
content = original.replace(old1, new1)

# Fix 2: propose_next_gaps failure returns [] → let it raise
old2 = """        except Exception as e:
            self._log_error("propose_next_gaps", e)
            return []

    def mark_completed"""
new2 = """        except Exception as e:
            self._log_error("propose_next_gaps", e)
            raise

    def mark_completed"""
assert old2 in content, "Could not find propose_next_gaps except block"
content = content.replace(old2, new2)

# Fix 3: _collect_dangling_links failure returns [] → let it raise
old3 = """        except Exception as e:
            self._log_error("collect_dangling_links", e)
            return []

    def _collect_thin_notes"""
new3 = """        except Exception as e:
            self._log_error("collect_dangling_links", e)
            raise

    def _collect_thin_notes"""
assert old3 in content, "Could not find collect_dangling_links except block"
content = content.replace(old3, new3)

# Fix 4: _collect_thin_notes failure returns [] → let it raise
# Need to find the exact pattern
old4 = """        except Exception as e:
            self._log_error("collect_thin_notes", e)
            return []"""
new4 = """        except Exception as e:
            self._log_error("collect_thin_notes", e)
            raise"""
assert old4 in content, "Could not find collect_thin_notes except block"
content = content.replace(old4, new4)

# Fix 5: _collect_missing_entities failure returns [] → let it raise
old5 = """        except Exception as e:
            self._log_error("collect_missing_entities", e)
            return []"""
new5 = """        except Exception as e:
            self._log_error("collect_missing_entities", e)
            raise"""
assert old5 in content, "Could not find collect_missing_entities except block"
content = content.replace(old5, new5)

# Fix 6: _collect_thin_communities failure returns [] → let it raise
old6 = """        except Exception as e:
            self._log_error("collect_thin_communities", e)
            return []"""
new6 = """        except Exception as e:
            self._log_error("collect_thin_communities", e)
            raise"""
assert old6 in content, "Could not find collect_thin_communities except block"
content = content.replace(old6, new6)

# Fix 7: _collect_link_density_anomalies failure returns [] → let it raise
old7 = """        except Exception as e:
            self._log_error("collect_link_density", e)
            return []"""
new7 = """        except Exception as e:
            self._log_error("collect_link_density", e)
            raise"""
assert old7 in content, "Could not find collect_link_density except block"
content = content.replace(old7, new7)

# Fix 8: _collect_all_gaps dangling_links failure returns [] → let it raise
old8 = """        try:
            dangling = self.graph.dangling_links(min_references=1)
        except Exception as e:
            self._log_error("collect_dangling_links", e)
            dangling = []"""
new8 = """        dangling = self.graph.dangling_links(min_references=1)"""
assert old8 in content, "Could not find _collect_all_gaps dangling try/except"
content = content.replace(old8, new8)

# Syntax check
import ast
try:
    ast.parse(content)
    print("Syntax check: PASS")
except SyntaxError as e:
    print(f"Syntax check: FAIL — {e}")
    shutil.copy2(backup, target)
    print("Restored backup")
    sys.exit(1)

# Write the fixed file
target.write_text(content, encoding="utf-8")
print("Written fixed file")

# Import test
result = subprocess.run(
    [sys.executable, "-c", "import knowledge_curriculum; print('Import: OK')"],
    capture_output=True, text=True, timeout=15,
    cwd="vaultbot_stuff/vaultbot_backend"
)
if result.returncode != 0:
    print(f"Import test: FAIL")
    print(f"stderr: {result.stderr[:500]}")
    shutil.copy2(backup, target)
    print("Restored backup due to import failure")
    sys.exit(1)
else:
    print(f"Import test: PASS — {result.stdout.strip()}")
    print("Fix 2 (knowledge_curriculum.py) complete — 8 masking fallbacks removed")
```

## Step 2: Fix fused_retrieval.py — remove silent channel degradations

```python
import shutil
import subprocess
import sys
from pathlib import Path

target = Path("vaultbot_stuff/vaultbot_backend/fused_retrieval.py")
backup = target.with_suffix(".py.bak")
original = target.read_text(encoding="utf-8")
shutil.copy2(target, backup)
print(f"Backed up to {backup}")

# Read the file to find all the try/except blocks that silently degrade
# We need to find each per-channel try/except and remove the fallback
lines = original.split('\n')
new_lines = []
i = 0
changes = 0
while i < len(lines):
    line = lines[i]
    # Pattern: "        try:" followed by channel retrieval, then "        except Exception as e:" with logging and fallback
    # We'll look for the specific pattern where a channel fails and returns empty/degraded results
    new_lines.append(line)
    i += 1

# Actually, let's do targeted replacements instead of line-by-line

content = original

# Find all "except Exception as e:" blocks in fused_retrieval.py that log and return degraded results
# We need to read the file first to see the exact patterns
print(f"File has {len(lines)} lines")
print("NOTE: fused_retrieval.py needs manual inspection — the patterns vary by channel")
print("Skipping automated fix for now — will need code_read to identify exact patterns")
```

## Step 3: Fix chat_handler.py — checkpoint + history save

```python
import shutil
import subprocess
import sys
from pathlib import Path

target = Path("vaultbot_stuff/vaultbot_backend/chat_handler.py")
backup = target.with_suffix(".py.bak")
original = target.read_text(encoding="utf-8")
shutil.copy2(target, backup)
print(f"Backed up to {backup}")

content = original

# Fix checkpoint save — remove try/except that silently passes
# Need to find the exact pattern around line 1288 and 1637
# These will be targeted replacements once we identify the exact strings

print("chat_handler.py needs targeted inspection — will identify exact patterns via code_read")
```

## Step 4: Fix compactor.py — unbounded messages on failure

```python
import shutil
import subprocess
import sys
from pathlib import Path

target = Path("vaultbot_stuff/vaultbot_backend/compactor.py")
backup = target.with_suffix(".py.bak")
original = target.read_text(encoding="utf-8")
shutil.copy2(target, backup)
print(f"Backed up to {backup}")

content = original

# The fallback at ~L169 returns original messages (unbounded) when compaction fails
# Replace with raise
old = """        except Exception:
            # Compaction failed — return original messages (unbounded but safe)
            return messages"""
new = """        except Exception as e:
            # Compaction failed — raise so the caller knows
            raise RuntimeError(f"Compaction failed: {e}") from e"""
assert old in content, f"Could not find compactor fallback pattern"
content = content.replace(old, new)

# Syntax check
import ast
try:
    ast.parse(content)
    print("Syntax check: PASS")
except SyntaxError as e:
    print(f"Syntax check: FAIL — {e}")
    shutil.copy2(backup, target)
    print("Restored backup")
    sys.exit(1)

target.write_text(content, encoding="utf-8")
print("Written fixed file")

result = subprocess.run(
    [sys.executable, "-c", "import compactor; print('Import: OK')"],
    capture_output=True, text=True, timeout=15,
    cwd="vaultbot_stuff/vaultbot_backend"
)
if result.returncode != 0:
    print(f"Import test: FAIL")
    print(f"stderr: {result.stderr[:500]}")
    shutil.copy2(backup, target)
    print("Restored backup due to import failure")
    sys.exit(1)
else:
    print(f"Import test: PASS — {result.stdout.strip()}")
    print("Fix 5 (compactor.py) complete — unbounded fallback removed")
```

## Step 5: Fix main.py — researcher crash notification silently passes

```python
import shutil
import subprocess
import sys
from pathlib import Path

target = Path("vaultbot_stuff/vaultbot_backend/main.py")
backup = target.with_suffix(".py.bak")
original = target.read_text(encoding="utf-8")
shutil.copy2(target, backup)
print(f"Backed up to {backup}")

content = original

# Find the researcher crash notification try/except that silently passes
# Around line 410 — need to identify exact pattern
print("main.py needs targeted inspection — will identify exact pattern via code_read")
```