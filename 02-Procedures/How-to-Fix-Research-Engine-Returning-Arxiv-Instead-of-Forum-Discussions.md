---
type: procedure
status: experimental
created: 2026-07-26
last_reviewed: 2026-07-26
review_interval_days: 90
success_count: 1
failure_count: 0
success_rate: 1.0
falsifiable_if: "a research query about a programming topic returns arxiv papers instead of forum discussions after this fix"
applies_to:
  - research
  - search-engine
depends_on:
  - "[[No-Wikipedia-Directive]]"
sources:
  - "https://api.github.com/search/issues"
  - "https://api.stackexchange.com/2.3/search/advanced"
---

# How to Fix Research Engine Returning Arxiv Instead of Forum Discussions

## When to Use This

Use this procedure when the research engine returns academic papers (arxiv.org) for technical/programming queries instead of developer forum discussions (GitHub issues, StackOverflow answers). The symptom: you search for "faiss remove_ids" and get gravitational wave papers.

## Root Causes

1. **No forum-specific backends.** The original FreeSearch fleet was DuckDuckGo Lite + Marginalia + arXiv. Only arXiv reliably returned results for technical queries, so everything came back as academic papers.
2. **`_keyterms()` destroyed `site:` operators.** The regex `[a-z][a-z0-9\-]+` strips the `:` character, so `site:github.com` became three separate tokens `site`, `github`, `com` — completely losing the domain targeting.
3. **arXiv always fired.** Every query was sent to arXiv regardless of whether it was a programming question. arXiv's `all:` search is extremely permissive and returns "results" for anything.
4. **No domain boosting.** Results were merged round-robin with no prioritization — arxiv results got equal weight with forum results.

## The Fix (3 files)

### 1. `forum_backends.py` (new file)

Created `GitHubIssuesBackend` and `StackOverflowBackend` — keyless, no-signup backends that search developer forums directly:
- **GitHub Issues**: `https://api.github.com/search/issues` — 10 req/min without auth, returns issue/PR bodies inline (no scraping needed)
- **StackOverflow**: `https://api.stackexchange.com/2.3/search/advanced` — 300 req/day without auth, returns question metadata (body scraped on demand)

Also added:
- `is_technical_query()` — detects programming queries via high-confidence signal terms (python, faiss, docker, github, etc.) and `site:` operators targeting developer forums
- `ForumEnhancedFreeSearch` — subclass of FreeSearch that adds forum backends to the fleet, skips arXiv for technical queries, and merges results with forum backends first

### 2. `main.py` (modified)

Swapped `from free_search import FreeSearch` to use `ForumEnhancedFreeSearch` with a try/except fallback to base `FreeSearch`.

### 3. `research_engine.py` (modified)

- `_keyterms()`: Extracts `site:domain.com` patterns BEFORE lowercasing/tokenizing, adds them to the result list with highest priority
- `_expand_query()`: Separates `site:` operators from regular terms, always appends them to the query (never dropped by the term cap)

## Verification

Tested with "faiss remove_ids IndexIDMap2 delete vectors performance":
- Before: 10 arxiv sources (gravitational waves, genetic algorithms, Kalman filters)
- After: 2 sources (1 GitHub Issues, 1 Marginalia), 0 arxiv sources

GitHub Issues found relevant results including:
- "fix(database): allocate FAISS ids monotonically, never from index.ntotal"
- "fix(consolidate): union-rebuild instead of IVF remove_ids to avoid FAISS abort"

## Related

- [[No-Wikipedia-Directive]] — source blocklist precedent
- [[How-to-Evaluate-Source-Credibility]] — source evaluation procedure
