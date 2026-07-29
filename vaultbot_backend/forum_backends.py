"""
Forum search backends for VaultBot's research engine.

Adds developer-forum-specific search:
  - GitHubIssuesBackend  — searches GitHub issues/PRs/discussions via the
                            public REST API (no auth, 10 req/min).
  - StackOverflowBackend — searches Stack Overflow Q&A via the public
                            Stack Exchange API (no auth, 300 req/day).
  - ForumEnhancedFreeSearch — subclass of FreeSearch that adds the forum
                            backends to the default fleet, skips arXiv for
                            technical queries, and prioritizes forum results
                            in the merge step.

Both backends are keyless, no-signup. ForumEnhancedFreeSearch is a drop-in
replacement for FreeSearch — same interface, just with forum backends added.

These backends exist because DuckDuckGo + Marginalia + arXiv alone return
academic papers for technical queries — GitHub issues and SO answers are the
"forums where nerds help each other" and were completely missing from the
search fleet.
"""

import re
import threading
import time
from typing import Any

import requests
from free_search import FreeSearch, _is_blocked_source


# ---------------------------------------------------------------------------
# Minimal backend infrastructure (mirrors free_search._Backend but standalone)
# ---------------------------------------------------------------------------
class _ForumBackend:
    """Throttle + cooldown scaffolding for forum backends.

    Mirrors free_search._Backend's interface but is self-contained to avoid
    circular imports. Subclasses implement _raw_search().
    """
    name = "base"
    min_interval = 1.0
    cooldown_seconds = 60.0
    ban_threshold = 3

    def __init__(self, session_logger=None, timeout: int = 20):
        self.session_logger = session_logger
        self.timeout = timeout
        self._last_request_time: float = 0.0
        self._lock = threading.Lock()
        self._cooldown_until: float = 0.0
        self._consecutive_failures: int = 0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.time()

    def _in_cooldown(self) -> bool:
        return time.time() < self._cooldown_until

    def _cooldown_remaining(self) -> float:
        """Seconds left in cooldown (0 if not cooling down).

        Mirrors free_search._Backend._cooldown_remaining so the /config
        endpoint can report cooldown state uniformly across all backends.
        """
        rem = self._cooldown_until - time.time()
        return max(0.0, rem)

    def _mark_failure(self, reason: str) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.ban_threshold:
                self._cooldown_until = time.time() + self.cooldown_seconds

    def _mark_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    @property
    def is_configured(self) -> bool:
        return True

    def search(self, query: str, max_results: int = 5
               ) -> tuple[list[dict[str, Any]], str | None]:
        """Return (results, error_or_None). Handles cooldown + throttle."""
        if self._in_cooldown():
            return [], f"cooldown:{int(self._cooldown_until - time.time())}s"
        self._throttle()
        try:
            raw = self._raw_search(query, max_results)
            self._mark_success()
            return raw, None
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in (403, 429, 503):
                self._mark_failure(f"http_{status}")
            else:
                self._mark_failure("http_error")
            return [], f"http_{status}"
        except Exception as e:
            self._mark_failure("exception")
            return [], str(e)

    def _raw_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        raise NotImplementedError


def _strip_site_operators(query: str) -> str:
    """Remove site:domain.com operators from a query string."""
    return re.sub(r"\bsite:\S+", "", query).strip()


# ---------------------------------------------------------------------------
# GitHub Issues — keyless REST API, 10 req/min without auth
# ---------------------------------------------------------------------------
class GitHubIssuesBackend(_ForumBackend):
    name = "github_issues"
    min_interval = 6.5          # ~9 req/min to stay under the 10/min limit
    cooldown_seconds = 120.0
    ban_threshold = 3

    API_URL = "https://api.github.com/search/issues"

    def _raw_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        q = _strip_site_operators(query)
        if not q:
            return []
        resp = requests.get(
            self.API_URL,
            params={"q": q, "per_page": min(max_results, 10),
                    "sort": "relevance", "order": "desc"},
            headers={"Accept": "application/vnd.github.v3+json",
                     "User-Agent": "VaultBot-Research"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[dict[str, Any]] = []
        for item in data.get("items", [])[:max_results]:
            url = item.get("html_url", "")
            if not url:
                continue
            title = item.get("title", "")
            body = (item.get("body") or "")[:3000]
            snippet = body[:400] if body else title
            results.append({
                "url": url,
                "title": title,
                "content": snippet,
                "raw_content": body,  # full issue/PR body — no scrape needed
            })
        return results


# ---------------------------------------------------------------------------
# Stack Overflow — keyless Stack Exchange API
# ---------------------------------------------------------------------------
class StackOverflowBackend(_ForumBackend):
    name = "stackoverflow"
    min_interval = 1.5
    cooldown_seconds = 60.0
    ban_threshold = 3

    API_URL = "https://api.stackexchange.com/2.3/search/advanced"

    def _raw_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        q = _strip_site_operators(query)
        if not q:
            return []
        resp = requests.get(
            self.API_URL,
            params={
                "order": "desc",
                "sort": "relevance",
                "q": q,
                "site": "stackoverflow",
                "pagesize": min(max_results, 10),
            },
            headers={"User-Agent": "VaultBot-Research"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[dict[str, Any]] = []
        for item in data.get("items", [])[:max_results]:
            url = item.get("link", "")
            if not url:
                continue
            title = item.get("title", "")
            tags = item.get("tags", [])
            snippet = (f"Score: {item.get('score', 0)}, "
                       f"Answers: {item.get('answer_count', 0)}, "
                       f"Tags: {', '.join(tags[:5])}")
            results.append({
                "url": url,
                "title": title,
                "content": snippet,
                "raw_content": "",  # SO API doesn't return body — scrape later
            })
        return results


# ---------------------------------------------------------------------------
# Technical query detection — when to skip arXiv
# ---------------------------------------------------------------------------

# High-confidence programming terms that almost never appear in non-technical
# contexts. If any of these are in the query, the query is technical and arXiv
# should be skipped (arXiv returns garbage for programming questions).
_TECHNICAL_SIGNALS = {
    # Languages
    "python", "javascript", "typescript", "rust", "golang", "kotlin",
    "swift", "ruby", "php", "c++", "c#", "scala", "julia", "perl", "lua",
    # Dev tools & platforms
    "github", "stackoverflow", "reddit", "gitlab", "bitbucket", "jenkins",
    "docker", "kubernetes", "npm", "yarn", "cargo", "maven", "gradle",
    "pip", "pypi", "webpack", "babel", "vscode", "intellij",
    # Libraries / frameworks
    "faiss", "pytorch", "tensorflow", "numpy", "pandas", "opencv", "scikit",
    "react", "vue", "angular", "django", "flask", "fastapi", "sqlalchemy",
    "langchain", "llama", "huggingface", "transformers", "ollama",
    "onnx", "onnxruntime", "beautifulsoup", "scrapy", "celery", "redis",
    "elasticsearch", "postgresql", "mysql", "sqlite", "mongodb",
    # Error / debug terms (high confidence for programming)
    "segfault", "traceback", "stacktrace", "compiler", "debugger",
    # FAISS-specific (our current use case)
    "indexidmap", "indexflat", "remove_ids", "add_with_ids",
}


def is_technical_query(query: str) -> bool:
    """Return True if the query is about programming/tools, not academia.

    Used by ForumEnhancedFreeSearch to decide whether to skip the arXiv
    backend — arXiv returns irrelevant academic papers for programming
    questions.
    """
    q_lower = query.lower()
    # site: operators targeting developer forums = definitely technical.
    if re.search(r"\bsite:(github|stackoverflow|reddit|gitlab)\b", q_lower):
        return True
    # Check for technical signal terms.
    tokens = set(re.findall(r"[a-z][a-z0-9_\-\+\.]+", q_lower))
    return bool(tokens & _TECHNICAL_SIGNALS)


# ---------------------------------------------------------------------------
# Academic query detection — when arXiv is actually useful
# ---------------------------------------------------------------------------
# arXiv's ``all:`` search is extremely permissive and returns "results" for
# almost any query (a paper mentioning "index" matches "lobby index in
# networks", one mentioning "vector" matches "fuzzy vectors"). The
# technical-query suppression above catches programming topics, but arXiv
# also returns garbage for procedural/how-to topics ("how to evaluate
# source credibility" → noise). arXiv should only fire when the query is
# GENUINELY academic: it carries research-paper signal terms (physics,
# mathematics, algorithm, neural, quantum, theorem, etc.) OR the topic is
# a named scientific concept. This makes arXiv opt-in by academic intent
# rather than the default it was.

# High-confidence academic / research-paper signal terms. A query carrying
# any of these is likely to have relevant arXiv results.
_ACADEMIC_SIGNALS = {
    # Scientific fields
    "physics", "mathematics", "biology", "chemistry", "neuroscience",
    "linguistics", "philosophy", "economics", "sociology", "psychology",
    "astronomy", "geology", "ecology", "genetics", "immunology",
    # Research-paper vocabulary
    "theorem", "lemma", "proof", "corollary", "algorithm", "complexity",
    "polynomial", "asymptotic", "convergence", "optimal", "bounds",
    "estimation", "inference", "regression", "classification", "embedding",
    "regularization", "optimization", "gradient", "stochastic",
    # ML / AI research terms
    "neural", "transformer", "attention", "reinforcement", "supervised",
    "unsupervised", "representation", "generative", "discriminative",
    "embedding", "encoder", "decoder", "backpropagation", "convolutional",
    "recurrent", "graph", "bayesian", "probabilistic", "statistical",
    # Quantum / physics technical
    "quantum", "entanglement", "hamiltonian", "eigenvalue", "tensor",
    "manifold", "topology", "differential", "integral",
    # arXiv section names that appear in real academic queries
    "cs.ai", "cs.cl", "cs.lg", "stat.ml", "cond-mat", "hep",
}

# Procedural / how-to / tooling prefixes that strongly indicate NON-academic
# intent. arXiv never helps with these.
_NON_ACADEMIC_PREFIXES = (
    "how to", "how-to", "best practices", "best-practices",
    "tutorial", "guide", "setup", "install", "configure", "deploy",
    "fix", "debug", "troubleshoot", "migrate",
)


def is_academic_query(query: str) -> bool:
    """Return True if the query is likely to have relevant arXiv results.

    arXiv's full-text search is permissive enough to return "results" for
    any query, so we gate it on genuine academic intent: either the query
    carries a research-paper signal term, OR it's a named scientific concept
    (not a how-to / procedural / tooling question).
    """
    q_lower = query.lower().strip()
    # Procedural / how-to prefixes → almost never academic.
    if any(q_lower.startswith(p) for p in _NON_ACADEMIC_PREFIXES):
        return False
    tokens = set(re.findall(r"[a-z][a-z0-9_\-\.\+]+", q_lower))
    return bool(tokens & _ACADEMIC_SIGNALS)


# ---------------------------------------------------------------------------
# ForumEnhancedFreeSearch — drop-in replacement for FreeSearch
# ---------------------------------------------------------------------------

# Merge priority: forum backends first, then general web, then academic.
# This ensures GitHub issues and SO answers appear at the top of results
# instead of being buried by arxiv papers or DDG's generic hits.
_MERGE_PRIORITY = [
    "github_issues", "stackoverflow",    # forums first
    "duckduckgo", "searxng", "marginalia",  # general web
    "arxiv",                              # academic last
]


class ForumEnhancedFreeSearch(FreeSearch):
    """FreeSearch with forum backends + arxiv suppression + priority merge.

    Drop-in replacement for FreeSearch. Adds GitHub Issues and StackOverflow
    backends to the default fleet, skips arXiv for technical queries (so
    programming questions don't get buried in academic papers), and merges
    results with forum backends first.
    """

    def __init__(self, session_logger=None, timeout: int = 20,
                 backends: list[Any] | None = None,
                 searxng_manager: Any = None):
        super().__init__(session_logger=session_logger, timeout=timeout,
                         backends=backends, searxng_manager=searxng_manager)
        # If caller provided custom backends, don't add forum backends — they
        # know what they want. Only augment the default fleet.
        if backends is None:
            self._backends.append(GitHubIssuesBackend(
                session_logger=session_logger, timeout=timeout))
            self._backends.append(StackOverflowBackend(
                session_logger=session_logger, timeout=timeout))

    def search(self, query: str, max_results: int = 5,
               search_depth: str = "advanced") -> dict[str, Any]:
        """Fan out to all backends in parallel, merge + dedupe.

        Overrides FreeSearch.search() to:
        1. Skip arXiv for technical queries (programming/tools).
        2. Merge with forum backends first (github_issues, stackoverflow)
           instead of round-robin, so forum results aren't buried.

        Returns {"results": [...], "unresponsive_engines": [...]}.
        """
        t0 = time.time()
        tech = is_technical_query(query)
        # arXiv is gated on genuine academic intent. arXiv's full-text
        # search is permissive enough to return "results" for any query
        # ("index" matches "lobby index in networks", "vector" matches
        # "fuzzy vectors"), so we only run it when the query carries real
        # research-paper signal (physics/theorem/neural/…). This replaces
        # the old "skip arXiv only for technical queries" approach, which
        # let arXiv fire on procedural/how-to topics and flood the pile
        # with off-topic papers. See [[How-to-Fix-Research-Engine-
        # Returning-Garbage]].
        academic = is_academic_query(query)
        # Forum backends (GitHub Issues, StackOverflow) are gated on
        # technical intent. GitHub's issue search is full of SEO spam
        # ("Dissertation Writing Help in UAE" repos) that pollutes
        # general/academic topics — the generic-quorum relevance gate
        # can't catch them because they deliberately stuff "academic",
        # "research", "sources" into their titles. Running them only for
        # technical queries (where they're genuinely the best source)
        # keeps the spam out of soft-topic research.
        forum_relevant = tech

        # Determine active backends:
        #  - arxiv only when the query is genuinely academic
        #  - forum backends only when the query is technical
        #  - general-web backends (ddg, searxng, marginalia) always run
        def _skip(b):
            if b.name == "arxiv":
                return not academic or tech
            if b.name in ("github_issues", "stackoverflow"):
                return not forum_relevant
            return False
        active_backends = [b for b in self._backends if not _skip(b)]

        results_by_backend: dict[str, tuple[list[dict[str, Any]], str | None]] = {}
        threads = []

        def _run(b):
            try:
                results_by_backend[b.name] = b.search(query, max_results)
            except Exception as e:
                results_by_backend[b.name] = ([], f"agg_error:{e}")

        for b in active_backends:
            th = threading.Thread(target=_run, args=(b,), daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join(timeout=max(self.timeout + 5, 30))

        unresponsive: list[list[str]] = []
        merged: list[dict[str, Any]] = []
        seen_urls: set = set()
        buckets: dict[str, list[dict[str, Any]]] = {}
        for name, (res, err) in results_by_backend.items():
            if err:
                unresponsive.append([name, err])
            if res:
                buckets[name] = res

        # Priority-ordered round-robin merge: forum backends first.
        ordered_names = [n for n in _MERGE_PRIORITY if n in buckets]
        # Include any backends not in the priority list (e.g. custom backends).
        ordered_names += [n for n in buckets if n not in _MERGE_PRIORITY]

        indices: dict[str, int] = {n: 0 for n in ordered_names}
        while any(indices[n] < len(buckets[n]) for n in ordered_names):
            for name in ordered_names:
                i = indices[name]
                if i >= len(buckets.get(name, [])):
                    continue
                r = buckets[name][i]
                indices[name] = i + 1
                url = r.get("url", "")
                if not url or url in seen_urls or _is_blocked_source(url):
                    continue
                seen_urls.add(url)
                r = dict(r, engine=name)
                merged.append(r)
                if len(merged) >= max_results * 2:
                    break
            if len(merged) >= max_results * 2:
                break

        out = merged[:max_results]
        if self.session_logger is not None:
            try:
                self.session_logger.log_tool_call(
                    tool="freesearch", method="search",
                    inputs={"query": query, "max": max_results,
                            "technical": tech},
                    outputs={"count": len(out),
                             "engines_up": len(results_by_backend) - len(unresponsive),
                             "engines_down": len(unresponsive)},
                    duration_ms=(time.time() - t0) * 1000)
            except Exception:
                pass
        return {"results": out, "unresponsive_engines": unresponsive}
