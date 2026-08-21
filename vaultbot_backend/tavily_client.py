"""
Tavily search client — the sole search backend for VaultBot.

Tavily is a paid API (with a free tier) purpose-built for AI agents: it
returns clean article content alongside the result URL, so the research
engine can often skip the fragile per-site scrape() step entirely.

This client exposes the same minimal surface the research engine needs:
  - search(query) -> {"results": [{"url","title","content","raw_content"}]}
  - scrape(url)   -> cleaned article text (direct fetch with realistic
                     headers, used when Tavily didn't return content inline)

A missing/invalid key makes search() return an empty result dict (with an
"unresponsive_engines" sentinel) so the research engine degrades gracefully
instead of crashing.
"""

import os
import time
from typing import Any

import requests


class TavilyClient:
    """Thin wrapper around the Tavily Search API.

    The key is read from the env (`TAVILY_API_KEY`) at construction time but
    can be swapped at runtime via `set_api_key()` so the GUI can update it
    without restarting the backend.
    """

    API_URL = "https://api.tavily.com/search"

    def __init__(
        self, api_key: str | None = None, session_logger=None, timeout: int = 20
    ):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        self.session_logger = session_logger
        self.timeout = timeout

    def _log(
        self,
        method: str,
        inputs: dict[str, Any] | None = None,
        outputs: Any = None,
        duration_ms: float | None = None,
        error: str | None = None,
    ):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(
            tool="tavily",
            method=method,
            inputs=inputs,
            outputs=outputs,
            duration_ms=duration_ms,
            error=error,
        )

    def set_api_key(self, key: str) -> None:
        """Update the API key at runtime (used by the /config endpoint)."""
        self.api_key = (key or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(
        self, query: str, max_results: int = 5, search_depth: str = "advanced"
    ) -> dict[str, Any]:
        """Run a Tavily search and return a SearXNG-compatible result dict.

        Returns {"results": [...], "unresponsive_engines": [...]}.
        Each result has url, title, content (snippet), and raw_content
        (full article text when Tavily scraped it).
        """
        t0 = time.time()
        if not self.is_configured:
            self._log(
                "search",
                {"query": query},
                error="no_api_key",
                duration_ms=(time.time() - t0) * 1000,
            )
            return {"results": [], "unresponsive_engines": [["tavily", "no_api_key"]]}
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": True,
        }
        try:
            resp = requests.post(self.API_URL, json=payload, timeout=self.timeout)
            if resp.status_code == 401:
                self._log(
                    "search",
                    {"query": query},
                    error="invalid_api_key",
                    duration_ms=(time.time() - t0) * 1000,
                )
                return {
                    "results": [],
                    "unresponsive_engines": [["tavily", "invalid_api_key"]],
                }
            resp.raise_for_status()
            data = resp.json()
            results = []
            for r in data.get("results", []):
                results.append(
                    {
                        "url": r.get("url", ""),
                        "title": r.get("title", ""),
                        "content": r.get("content", ""),  # snippet
                        "raw_content": r.get("raw_content", ""),  # full article
                    }
                )
            self._log(
                "search",
                {"query": query, "depth": search_depth},
                outputs={"result_count": len(results)},
                duration_ms=(time.time() - t0) * 1000,
            )
            return {"results": results, "unresponsive_engines": []}
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            self._log(
                "search",
                {"query": query},
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )
            return {"results": [], "unresponsive_engines": [["tavily", str(e)]]}

    def scrape(self, url: str, timeout: int = 12) -> str:
        """Return article text for a URL.

        Tavily returns raw_content inline in search results, so this is only
        used for follow-up scrapes of URLs Tavily didn't return content for.
        We don't re-query Tavily for a single URL (wasteful); instead we do a
        direct fetch with realistic headers. Falls back to "" on failure.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            resp = requests.get(
                url, headers=headers, timeout=timeout, allow_redirects=True
            )
            resp.raise_for_status()
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(
                ["script", "style", "nav", "footer", "header", "aside", "form"]
            ):
                tag.decompose()
            main = soup.find("article") or soup.find("main") or soup.find("body")
            text = (
                main.get_text(separator="\n", strip=True)
                if main
                else soup.get_text(separator="\n", strip=True)
            )
            return text[:20000]
        except Exception:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            return ""
