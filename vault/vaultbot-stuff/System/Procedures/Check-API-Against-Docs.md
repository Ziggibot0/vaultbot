---
type: procedure
status: active
baseline: true
model_cartridge: big
created: 2026-08-22
description: "Verify a code change's external API usage against official documentation. Fetches the authoritative docs for every non-VaultBot module the change imports, then checks the four code-hallucination modes (wrong name, wrong signature, deprecated pattern, nonexistent import) against them. Produces a doc_source URL list that safe_write requires."
when_to_use: "Before any safe_write that imports a stdlib or third-party module. Run this to prove the edit matches the official docs instead of model weights."
falsifiable_if: "it reports an API as doc-verified when the docs contradict it, or flags a correct API as wrong"
applies_to:
  - code-verification
  - anti-hallucination
  - provenance
  - self-modification
allowed_tools:
  - code_read
  - vault_research
  - llm_generate
summary: Check-API-Against-Docs
tags:
  - procedure
  - procedures
  - code-verification
  - anti-hallucination
---

# Check-API-Against-Docs

## Purpose

Prove that a code change's use of external APIs matches the official
documentation — not the model's training weights. This is the code
analogue of the chat closed-set citation gate: an edit that can't point
at a real doc source is rejected by `safe_write`, and this procedure is
what produces that source.

## Why This Exists

`safe_write` verifies code *syntactically* (parses, imports, passes tests)
but never *semantically* — "it runs without crashing" is not "it's
correct." The four ways a model hallucinates code are all checkable
against docs:

1. **Wrong function name** — the name doesn't exist in the docs.
2. **Wrong signature/arity** — the call passes the wrong arguments.
3. **Deprecated pattern** — the docs mark the API as deprecated/removed.
4. **Nonexistent import** — the module/name isn't real.

This procedure fetches the authoritative docs for every external module
the change imports and checks the change against them, producing the
`doc_source` URL list that `safe_write` requires.

## Inputs

- `file_path` (string, required): the file being edited (relative to vault root).
- `new_content` (string, required): the proposed new file content.

## Output Contract

Returns a JSON object with `doc_source` (list of official-docs URLs) and
`verdict` ("verified" | "hallucinated") plus per-import findings.

---

## Steps

### Step 1: Extract the external imports from the proposed content

1. ```python
import ast, json

new_content = args.get("new_content", "")
if not new_content:
    print(json.dumps({"error": "new_content argument required"}))
    exit(1)

# Internal modules = the backend's own .py stems + its packages.
import os
backend_dir = os.path.join(os.environ.get("VAULT_PATH", "."), "vaultbot_backend")
internal = set()
if os.path.isdir(backend_dir):
    for f in os.listdir(backend_dir):
        if f.endswith(".py"):
            internal.add(f[:-3])
internal |= {"routers", "custom_tools", "identity"}

external = []
try:
    tree = ast.parse(new_content)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top not in internal and top not in external:
                    external.append(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module is None:
                continue
            top = node.module.split(".")[0]
            if top not in internal and top not in external:
                external.append(top)
except SyntaxError as e:
    print(json.dumps({"error": f"SyntaxError: {e}"}))
    exit(1)

print(json.dumps({"external_imports": external}))
```

### Step 2: Fetch official docs for each external module

2. ```python
import json

data = json.loads(output)
external = data.get("external_imports", [])

if not external:
    print(json.dumps({"doc_source": [], "verdict": "verified",
                      "note": "no external imports — nothing to prove"}))
    exit(0)

# Map each module to its authoritative docs domain. This is the
# source_allowlist that keeps the dig on official docs only.
DOC_DOMAINS = {
    "requests": "docs.python-requests.org",
    "bs4": "www.crummy.com",
    "beautifulsoup4": "www.crummy.com",
    "numpy": "numpy.org",
    "pandas": "pandas.pydata.org",
    "scipy": "docs.scipy.org",
    "matplotlib": "matplotlib.org",
    "sklearn": "scikit-learn.org",
    "fastapi": "fastapi.tiangolo.com",
    "pydantic": "docs.pydantic.dev",
    "sqlalchemy": "docs.sqlalchemy.org",
    "aiohttp": "docs.aiohttp.org",
    "httpx": "www.python-httpx.org",
    "pytest": "docs.pytest.org",
    "flask": "flask.palletsprojects.com",
    "django": "docs.djangoproject.com",
    "jinja2": "jinja.palletsprojects.com",
    "click": "click.palletsprojects.com",
    "rich": "rich.readthedocs.io",
    "typer": "typer.tiangolo.com",
    "pydantic_settings": "docs.pydantic.dev",
    "dotenv": "pypi.org",
    "yaml": "pyyaml.org",
    "toml": "docs.python.org",
    "pathlib": "docs.python.org",
    "asyncio": "docs.python.org",
    "subprocess": "docs.python.org",
    "json": "docs.python.org",
    "re": "docs.python.org",
    "os": "docs.python.org",
    "sys": "docs.python.org",
    "datetime": "docs.python.org",
    "typing": "docs.python.org",
    "collections": "docs.python.org",
    "itertools": "docs.python.org",
    "functools": "docs.python.org",
    "contextlib": "docs.python.org",
    "dataclasses": "docs.python.org",
    "enum": "docs.python.org",
    "hashlib": "docs.python.org",
    "urllib": "docs.python.org",
    "http": "docs.python.org",
    "logging": "docs.python.org",
    "threading": "docs.python.org",
    "queue": "docs.python.org",
    "tempfile": "docs.python.org",
    "shutil": "docs.python.org",
    "time": "docs.python.org",
    "math": "docs.python.org",
    "random": "docs.python.org",
    "string": "docs.python.org",
    "textwrap": "docs.python.org",
    "argparse": "docs.python.org",
    "abc": "docs.python.org",
    "copy": "docs.python.org",
    "pickle": "docs.python.org",
    "sqlite3": "docs.python.org",
    "csv": "docs.python.org",
    "io": "docs.python.org",
    "gzip": "docs.python.org",
    "zipfile": "docs.python.org",
    "base64": "docs.python.org",
    "uuid": "docs.python.org",
    "secrets": "docs.python.org",
    "struct": "docs.python.org",
    "socket": "docs.python.org",
    "ssl": "docs.python.org",
    "email": "docs.python.org",
    "html": "docs.python.org",
    "xml": "docs.python.org",
    "unittest": "docs.python.org",
    "traceback": "docs.python.org",
    "warnings": "docs.python.org",
    "weakref": "docs.python.org",
    "types": "docs.python.org",
    "inspect": "docs.python.org",
    "importlib": "docs.python.org",
    "pkgutil": "docs.python.org",
    "glob": "docs.python.org",
    "fnmatch": "docs.python.org",
    "statistics": "docs.python.org",
    "decimal": "docs.python.org",
    "fractions": "docs.python.org",
    "heapq": "docs.python.org",
    "bisect": "docs.python.org",
    "array": "docs.python.org",
    "mmap": "docs.python.org",
    "signal": "docs.python.org",
    "atexit": "docs.python.org",
    "getpass": "docs.python.org",
    "platform": "docs.python.org",
    "resource": "docs.python.org",
    "multiprocessing": "docs.python.org",
    "concurrent": "docs.python.org",
    "asyncio": "docs.python.org",
    "select": "docs.python.org",
    "selectors": "docs.python.org",
    "errno": "docs.python.org",
    "ctypes": "docs.python.org",
    "curses": "docs.python.org",
    "readline": "docs.python.org",
    "shlex": "docs.python.org",
    "configparser": "docs.python.org",
    "netrc": "docs.python.org",
    "plistlib": "docs.python.org",
    "shelve": "docs.python.org",
    "dbm": "docs.python.org",
    "zlib": "docs.python.org",
    "bz2": "docs.python.org",
    "lzma": "docs.python.org",
    "tarfile": "docs.python.org",
    "fileinput": "docs.python.org",
    "filecmp": "docs.python.org",
    "linecache": "docs.python.org",
    "tokenize": "docs.python.org",
    "keyword": "docs.python.org",
    "symbol": "docs.python.org",
    "ast": "docs.python.org",
    "symtable": "docs.python.org",
    "codecs": "docs.python.org",
    "unicodedata": "docs.python.org",
    "stringprep": "docs.python.org",
    "difflib": "docs.python.org",
    "pprint": "docs.python.org",
    "reprlib": "docs.python.org",
    "graphlib": "docs.python.org",
    "zoneinfo": "docs.python.org",
    "calendar": "docs.python.org",
    "gettext": "docs.python.org",
    "locale": "docs.python.org",
}

# Group modules by their doc domain so we do ONE dig per domain.
from collections import defaultdict
by_domain = defaultdict(list)
unmapped = []
for mod in external:
    dom = DOC_DOMAINS.get(mod)
    if dom:
        by_domain[dom].append(mod)
    else:
        unmapped.append(mod)

print(json.dumps({
    "by_domain": {k: v for k, v in by_domain.items()},
    "unmapped": unmapped,
}))
```

### Step 3: Research each domain's docs (authoritative-only)

3. ```python
import json

data = json.loads(output)
by_domain = data.get("by_domain", {})
unmapped = data.get("unmapped", [])

doc_sources = []
findings = []

for domain, mods in by_domain.items():
    topic = "official API reference for: " + ", ".join(mods)
    r = vault_research(topic, depth="quick", source_allowlist=[domain])
    if r and not r.get("error"):
        # Collect the source URLs as the doc_source list.
        for s in r.get("sources", []):
            url = s.get("url", "")
            if url and url not in doc_sources:
                doc_sources.append(url)
        findings.append({
            "domain": domain,
            "modules": mods,
            "synthesis": r.get("synthesis", "")[:3000],
        })
    else:
        findings.append({
            "domain": domain,
            "modules": mods,
            "error": (r or {}).get("error", "research failed"),
        })

if unmapped:
    findings.append({
        "domain": None,
        "modules": unmapped,
        "note": "no doc-domain mapping — verify manually against the module's official docs",
    })

print(json.dumps({"doc_sources": doc_sources, "findings": findings}))
```

### Step 4: Verify the change against the docs (the hallucination check)

4. [llm: You are a code-vs-documentation verifier. Given the proposed code
   change and the official documentation fetched for its external imports,
   check the change for the four code-hallucination modes:

   1. WRONG NAME — a function/class/attribute name that does not exist in
      the docs.
   2. WRONG SIGNATURE — a call that passes the wrong number/type of
      arguments per the docs.
   3. DEPRECATED — an API the docs mark as deprecated or removed.
   4. NONEXISTENT IMPORT — a module or name that is not real.

   For each external import, state whether the change's usage is VERIFIED
   (matches the docs) or HALLUCINATED (contradicts the docs), with a
   one-line reason citing the doc. If the docs are missing or inconclusive,
   say UNVERIFIED — do NOT guess. Output JSON only:

   {"verdict": "verified" | "hallucinated" | "unverified",
    "findings": [{"module": ..., "status": ..., "reason": ...}]}

   Proposed change:
   {new_content}

   Documentation findings:
   {findings}]

### Step 5: Emit the doc_source and verdict

5. ```python
import json

# The doc_sources from step 3 are in the step-3 output; the verdict is in
# the step-4 output. Combine them into the final contract.
# (The runtime exposes prior step outputs via `output`; reconstruct here.)
print(json.dumps({
    "verdict": "see step 4",
    "doc_source": "see step 3",
    "note": "Prove-Code-Change consumes steps 3 and 4 to build the final doc_source.",
}))
```

## Related

- [[Prove-Code-Change]] — the orchestrator that runs this and then calls safe_write
- [[Safe-Write]] — the write tool that requires the doc_source this produces
- [[Choose-Write-Tool]] — routes to the correct write tool first
- [[Cite-Provenance]] — the chat-side analogue (claims must cite vault notes)
