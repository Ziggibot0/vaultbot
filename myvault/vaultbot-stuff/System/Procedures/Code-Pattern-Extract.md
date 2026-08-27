---
type: procedure
status: experimental
baseline: true
created: 2026-08-02
description: Scan backend code for a specific pattern (e.g., all try/except blocks, all async functions, all places a specific module is imported) and return the matches with file, line, and context. Given a pattern description, the small model translates it to a regex and searches the code. Use when looking for code patterns across the backend.
when_to_use: when looking for all instances of a code pattern, when finding all try/except blocks, when finding all uses of a module, when auditing code patterns, or when asked 'where does the code do X'
falsifiable_if: the procedure returns matches that don't fit the pattern, or misses matches
applies_to:
  - code-search
  - pattern-matching
  - self-modification
  - code-audit
allowed_tools:
  - code_read
  - llm_generate
summary: Code-Pattern-Extract
tags:
  - procedure
  - procedures
---

# Code-Pattern-Extract

## When to Run This

When you need to find all instances of a code pattern across the backend
— all try/except blocks, all async functions, all imports of a specific
module. The small model translates your pattern description into a regex
and searches.

## Why This Exists

Finding all instances of a code pattern across the backend required manual grepping with no structured results. This procedure translates a pattern description into a regex and returns matches with file, line, and context. The key tradeoff is that the small model translates the description to a regex, so the search is flexible but depends on a correct translation.

## Steps

### Step 1: Small model translates the pattern description to a regex

1. ```python
import json

pattern_desc = args.get("pattern", "")
if not pattern_desc:
    result = json.dumps({"error": "pattern argument required"})
else:
    prompt = f"""Translate this code pattern description into a Python regex
that would match it in source code:

Pattern: {pattern_desc}

Common patterns:
- try/except blocks: r'try:\\n.*?except\\s+\\w+.*?:'
- async functions: r'async\\s+def\\s+\\w+'
- imports of X: r'(?:from\\s+X\\s+import|import\\s+X)'
- function calls to X: r'\\bX\\s*\\('
- decorator: r'@\\w+'

Return JSON: {{"regex": "the regex pattern", "explanation": "what it matches"}}
Return ONLY the JSON."""
    regex_result = llm_generate(prompt)
    result = regex_result
```

### Step 2: Search the backend with the regex

2. ```python
import json as _json, re

data = _json.loads(output)
if "error" in data:
    result = output
else:
    try:
        pattern = data["regex"]
        regex = re.compile(pattern, re.MULTILINE | re.DOTALL)
    except Exception as e:
        result = _json.dumps({"error": f"invalid regex: {e}"})
    else:
        backend_dir = Path(FRAMEWORK_ROOT) / "vaultbot_backend"
        matches = []
        for py in backend_dir.rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            rel = str(py.relative_to(backend_dir))
            for m in regex.finditer(text):
                line_num = text[:m.start()].count('\n') + 1
                # Get context (3 lines around match)
                lines = text.split('\n')
                start_line = max(0, line_num - 2)
                end_line = min(len(lines), line_num + 3)
                context = '\n'.join(lines[start_line:end_line])
                matches.append({"file": rel, "line": line_num,
                                "match": m.group()[:200],
                                "context": context[:300]})
                if len(matches) >= 30:
                    break
            if len(matches) >= 30:
                break
        result = _json.dumps({"matches": matches, "total": len(matches),
                              "pattern": pattern})
```

### Step 3: Return the matches

3. ```python
import json as _json

data = _json.loads(output)
if "error" in data:
    result = data
else:
    result = _json.dumps({
        "matches": data.get("matches", []),
        "total_matches": data.get("total", 0),
        "pattern_searched": data.get("pattern", ""),
    })
```

## Related

- [[Codebase-Map]] — static index of the backend this searches
- [[Analyze-Function-Flow]] — traces a function's call graph
- [[Chat-Consolidation]] — dispatches to this for build-log chats