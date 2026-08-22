---
type: procedure
status: active
baseline: true
model_cartridge: small
created: 2026-08-18
description: "Diagnose and fix the general class of problem where the research engine produces notes with hallucinated, dead, or irrelevant source links. Covers: (a) URLs that 404 or point to nothing, (b) GitHub issues/PRs passed off as authoritative sources, (c) notes with '- none' in Sources, (d) notes with no Sources section at all. Checks URL liveness, source domain credibility, synthesis source-list integrity, and whether the note was produced by the research engine or hallucinated directly by the LLM. Use when sources look wrong, links are dead, or the vaultbot is 'drawing from bullshit'."
when_to_use: "when research note sources are dead links, when GitHub repos appear as sources, when a note has no Sources section or says '- none', when the vaultbot seems to be hallucinating sources, after changing the research engine source filtering pipeline"
falsifiable_if: "it reports a URL as dead but the URL actually resolves (verifiable by opening it in a browser), or it reports a source as hallucinated but the note was produced by the research engine (verifiable by the <!-- research: N sources --> comment)"
applies_to:
  - research-quality
  - source-verification
  - troubleshooting
  - url-liveness
  - anti-hallucination
allowed_tools:
  - code_run
  - vault_list
summary: Diagnose-Hallucinated-Sources
tags:
  - procedure
  - procedures
  - troubleshooting
  - research
  - sources
  - hallucination
---

# Diagnose-Hallucinated-Sources

Diagnoses the general class of "the research engine produced a note with
bad sources" problems. This covers four distinct failure modes that all
look the same to the user ("the links go to nothing"):

1. **Dead URLs**: The search engine returned a URL that 404s or doesn't
   resolve. The research engine had no liveness check, so the dead link
   went straight into the note's Sources section.
2. **GitHub issue/PR sources**: Search engines return GitHub issues and
   PRs because their titles match the query keywords. But a GitHub issue
   titled "Implement OAuth2" is a project planning document, not
   documentation about OAuth2. These pass the relevance gate (they
   contain the signal terms) but carry no authority.
3. **Empty Sources section ("- none")**: The LLM synthesis produced a
   full research note with `[sources: ...]` citations in the text, but
   the Sources section says "- none". This means the LLM hallucinated
   claims from its training data and cited source titles that don't
   correspond to real URLs in the source list.
4. **No research engine at all**: The note has no `<!-- research: N
   sources -->` comment and no Sources section. The vaultbot LLM wrote
   it directly from training data, presented as research. This is pure
   hallucination.

## When to Run This

- Research note sources are dead links (404, parking pages, timeouts)
- GitHub repos/issues/PRs appear as sources for factual claims
- A research note has "## Sources\n\n- none"
- A research note has no Sources section at all
- The vaultbot "is drawing from bullshit" or "can't see that it's wrong"
- After changing the source filtering / URL liveness pipeline

## Why This Exists

Research notes with dead, low-credibility, or absent source links all look the same to the user ("the links go to nothing") but stem from four distinct failure modes: dead URLs, GitHub issue/PR sources, empty Sources sections, and notes written directly by the LLM with no research engine at all. This procedure exists to classify which mode applies so the right fix (delete, re-research, or repair URLs) is chosen. The key tradeoff is distinguishing notes that predate the liveness/classification fixes from notes that indicate a still-broken pipeline.

## Inputs

- `note_path` (required): the vault-relative path to the research note
  to diagnose (e.g. `vaultbot/Knowledge/Research/Google-OAuth-20-...md`)

## Steps

### Step 1: Load the note and extract its source URLs

```python
import json, re, os
from pathlib import Path

note_path = args.get("note_path", "").strip()
if not note_path:
    raise ValueError("note_path is required")

# Resolve relative to vault root
vault_root = Path(vault_path)
full_path = vault_root / note_path if not os.path.isabs(note_path) else Path(note_path)

if not full_path.exists():
    raise FileNotFoundError(f"Note not found: {full_path}")

content = full_path.read_text(encoding="utf-8")

# Extract all markdown links from the Sources section
sources_section = ""
in_sources = False
for line in content.split("\n"):
    if line.strip().startswith("## Sources"):
        in_sources = True
        continue
    if in_sources and line.strip().startswith("##"):
        break
    if in_sources:
        sources_section += line + "\n"

# Extract URLs from markdown links: [Title](URL)
source_urls = re.findall(r'\[([^\]]*)\]\(([^)]+)\)', sources_section)
# Also extract URLs from [sources: ...] inline citations
inline_citations = re.findall(r'\[sources:\s*([^\]]+)\]', content)

# Check for the research engine comment
research_comment = re.search(r'<!-- research:\s*(\d+)\s+sources', content)

# Check for "- none" in sources
has_none = bool(re.search(r'^\s*-\s*none\s*$', sources_section, re.MULTILINE))

# Check for no Sources section at all
has_sources_section = "## Sources" in content

result = json.dumps({
    "note": str(full_path.name),
    "has_sources_section": has_sources_section,
    "has_research_comment": bool(research_comment),
    "research_source_count": int(research_comment.group(1)) if research_comment else None,
    "has_none_source": has_none,
    "source_urls": [{"title": t, "url": u} for t, u in source_urls],
    "source_url_count": len(source_urls),
    "inline_citation_count": len(inline_citations),
    "inline_citations_sample": inline_citations[:10],
}, indent=2)
```

### Step 2: Check URL liveness for all source URLs

```python
import requests, threading

urls_to_check = [u for _, u in source_urls]
results = {}
sem = threading.Semaphore(5)

def check(url):
    with sem:
        try:
            resp = requests.head(url, timeout=5, allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            if resp.status_code == 405:
                resp = requests.get(url, timeout=5, allow_redirects=True, stream=True,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                resp.close()
            results[url] = {"alive": 200 <= resp.status_code < 400, "status": resp.status_code}
        except requests.exceptions.Timeout:
            results[url] = {"alive": False, "status": "timeout"}
        except Exception as e:
            results[url] = {"alive": False, "status": f"error:{type(e).__name__}"}

threads = [threading.Thread(target=check, args=(u,), daemon=True) for u in urls_to_check]
for t in threads: t.start()
for t in threads: t.join(timeout=10)

dead = {u: r for u, r in results.items() if not r["alive"]}
alive = {u: r for u, r in results.items() if r["alive"]}

# Check for GitHub issue/PR URLs
github_issue_urls = [u for u in urls_to_check if "github.com" in u.lower()
    and re.search(r'github\.com/[^/]+/[^/]+/(issues|pull|wiki|discussions)/', u.lower())]

result = json.dumps({
    "total_urls": len(urls_to_check),
    "alive": len(alive),
    "dead": len(dead),
    "dead_urls": [{"url": u, "status": r["status"]} for u, r in dead.items()],
    "github_issue_urls": github_issue_urls,
    "github_issue_count": len(github_issue_urls),
}, indent=2)
```

### Step 3: Diagnose and classify the failure mode

[llm: Based on the outputs of steps 1 and 2, classify the note's source problem into one or more of these failure modes:

**Failure Mode A — Dead URLs**: Source URLs that return 404/timeout/connection_error. These are links to nothing. The URL liveness check (url_liveness.py) should have caught these, but the note was created before the check was added OR the check failed.

**Failure Mode B — GitHub issue/PR sources**: URLs pointing to github.com/owner/repo/issues/ or /pull/ — these are project planning documents, not authoritative sources. The source_classification.py is_github_issue_or_pr() filter should have skipped these, but the note was created before the filter was added.

**Failure Mode C — Empty Sources ("- none")**: The note has a Sources section but it says "- none". The LLM synthesis hallucinated claims from training data and didn't use the provided source list. The research_synthesizer.py prompt update should prevent this, but the note predates the fix.

**Failure Mode D — No research engine**: The note has no `<!-- research: N sources -->` comment and no Sources section. The vaultbot LLM wrote it directly from training data — pure hallucination presented as research. This note should be deleted or rewritten using the research engine.

Report: (1) which failure modes apply, (2) which specific URLs are dead or low-credibility, (3) whether the note predates the fixes (check the `created:` date in frontmatter vs 2026-08-18), (4) recommended action: delete the note, re-research the topic, or fix the specific URLs.]

### Step 4: Validate

[validate: output contains at least one of "Failure Mode A", "Failure Mode B", "Failure Mode C", "Failure Mode D" or "no issues found"]

## Related

- [[Diagnose-Retrieval-Failure]] — sibling diagnosis for notes that should surface but don't
- [[Evaluate-Retrieval]] — measures retrieval quality that feeds source selection
- [[Verify-Claims]] — verifies the claims a research note makes against its sources