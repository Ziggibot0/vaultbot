---
type: procedure
status: experimental
model_cartridge: small
created: 2026-08-02
description: "Read a code file and extract its structure: all function/class signatures with line numbers, imports, and a one-line summary of each function. Given a file path, returns a structured map of the file's contents. Use when you need to navigate a large file and don't want to read all of it."
when_to_use: "when navigating a large code file, when you need to find a specific function in a file, when you need an overview of a file's structure before reading specific sections, or when looking for where to make an edit"
falsifiable_if: "the structure map misses functions/classes, or the summaries contradict the code"
applies_to:
  - code-comprehension
  - code-navigation
  - self-modification
  - file-mapping
allowed_tools:
  - code_read
  - llm_generate
---

# Smart-Code-Read

## When to Run This

When a code file is too large to read all at once, run this to get a
structured map: every function/class, its line range, imports, and a
one-line summary. Then use `code_read` with the specific line range.

## Steps

### Step 1: Read the file and extract structure deterministically

1. ```python
import re, json

file_path = args.get("file_path", "")
if not file_path:
    result = json.dumps({"error": "file_path argument required"})
else:
    p = Path(file_path)
    if not p.exists():
        p = Path(vault_path) / "vaultbot_stuff" / "vaultbot_backend" / file_path
    if not p.exists():
        result = json.dumps({"error": f"file not found: {file_path}"})
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.split('\n')
        # Extract imports
        imports = [l.strip() for l in lines if l.strip().startswith(('import ', 'from '))][:20]
        # Extract function/class signatures
        sigs = []
        for i, line in enumerate(lines, 1):
            m = re.match(r'^(\s*)((?:async\s+)?(?:def|class)\s+(\w+))', line)
            if m:
                sigs.append({"line": i, "kind": "class" if "class" in m.group(2) else "def",
                             "name": m.group(3), "signature": line.strip()[:120]})
        result = json.dumps({"file": str(p), "total_lines": len(lines),
                             "imports": imports, "signatures": sigs[:40]})
```

### Step 2: Small model summarizes each function

2. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = output
else:
    sigs = data.get("signatures", [])
    if not sigs:
        result = _json.dumps({"error": "no functions/classes found"})
    else:
        # Read snippets around each signature for context
        text_lines = Path(data["file"]).read_text(encoding="utf-8", errors="replace").split('\n')
        func_summaries = []
        for s in sigs[:20]:
            start = s["line"] - 1
            end = min(start + 15, len(text_lines))
            snippet = '\n'.join(text_lines[start:end])
            func_summaries.append({"name": s["name"], "line": s["line"],
                                   "kind": s["kind"], "snippet": snippet[:300]})

        prompt = f"""For each function/class, write a one-line summary of what it does.

{json.dumps(func_summaries, indent=2)}

Return JSON: [{{"name": "...", "line": N, "summary": "one line"}}]
Return ONLY the JSON array."""
        summaries = llm_generate(prompt)
        result = summaries
```

### Step 3: Return the structure map

3. ```python
import json as _json
try:
    start = output.find("[")
    end = output.rfind("]")
    parsed = _json.loads(output[start:end+1]) if start != -1 else []
except Exception:
    parsed = []
result = _json.dumps({"file": data.get("file"), "total_lines": data.get("total_lines"),
                      "imports": data.get("imports", []),
                      "structure": parsed})
```