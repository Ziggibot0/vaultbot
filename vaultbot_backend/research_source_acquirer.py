import time
from typing import Any


class ResearchSourceAcquirer:
    def __init__(self, owner: Any, compatibility_module: Any):
        self.owner = owner
        self.compatibility_module = compatibility_module

    def _log(self, event: str, **data: Any) -> None:
        self.owner._log(event, data)

    def search_round(
        self,
        query: str,
        round_idx: int,
        topic: str = "",
        source_allowlist: list[str] | None = None,
        source_denylist: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        owner = self.owner
        compatibility = self.compatibility_module
        started_at = time.time()

        if not (owner.search_client and owner.search_client.is_configured):
            self._log("research_search_unconfigured", round=round_idx, query=query)
            return []

        try:
            results = owner.search_client.search(
                query, max_results=owner.max_sources_per_round
            )
        except Exception as error:  # noqa: BLE001 -- surfaced in the research event log
            backend = getattr(owner.search_client, "name", "search_client")
            self._log(
                "research_search_failed",
                round=round_idx,
                query=query,
                backend=backend,
                error=str(error),
            )
            results = {
                "results": [],
                "unresponsive_engines": [[backend, str(error)]],
            }
        hits = results.get("results", [])[: owner.max_sources_per_round]
        self._log(
            "research_search",
            round=round_idx,
            query=query,
            backend=getattr(owner.search_client, "name", "search_client"),
            hits=len(hits),
            duration_ms=(time.time() - started_at) * 1000,
        )

        topic_terms = compatibility._keyterms(topic) if topic else []
        signal = compatibility._signal_terms(topic_terms)
        base_signal_count = len(signal)
        compounds = compatibility._compound_signals(topic)
        existing = {term.lower() for term in signal}
        for compound in compounds:
            if compound not in existing:
                signal.append(compound)
                existing.add(compound)

        sources = []
        for hit in hits:
            url = hit.get("url")
            if not url:
                continue
            if compatibility._is_blocked_source(url):
                self._log("research_source_blocked", round=round_idx, url=url)
                continue
            if not compatibility._is_allowlisted(url, source_allowlist):
                self._log("research_source_not_allowlisted", round=round_idx, url=url)
                continue
            if compatibility._is_denylisted(url, source_denylist):
                self._log("research_source_denylisted", round=round_idx, url=url)
                continue

            text = hit.get("raw_content", "") or ""
            snippet = hit.get("content", "")
            try:
                from web_source_store import fetch_and_save, save_source

                if text and len(text) >= 80:
                    save_source(url, text, title=hit.get("title", ""), topic=topic)
                else:
                    fetch_and_save(url, title=hit.get("title", ""), topic=topic)
            except Exception as error:  # noqa: BLE001 -- archive failure is logged
                self._log("research_archive_failed", url=url, error=str(error))

            if not text or len(text) < 80:
                owner._progress(
                    "scraping",
                    {"round": round_idx, "url": url, "title": hit.get("title", "")},
                )
                try:
                    text = owner.search_client.scrape(
                        url, timeout=int(owner.scrape_timeout)
                    )
                except Exception as error:  # noqa: BLE001 -- scrape failure is logged
                    self._log("research_scrape_failed", url=url, error=str(error))
                    text = ""
            if not text or len(text) < 30:
                continue

            gate_text = text if len(text) >= 200 else (f"{snippet}\n{text}")
            relevance, reason = compatibility._source_relevance(
                hit.get("title", ""),
                gate_text,
                signal,
                topic_terms,
                url=url,
                base_signal_count=base_signal_count,
            )
            if relevance < 1.0:
                self._log(
                    "research_source_rejected",
                    round=round_idx,
                    url=url,
                    title=hit.get("title", "")[:80],
                    score=round(relevance, 2),
                    reason=reason,
                )
                continue

            is_low_credibility = compatibility._is_low_credibility_domain(url)
            if compatibility._is_github_issue_or_pr(url):
                self._log(
                    "research_source_skipped_github_issue",
                    round=round_idx,
                    url=url,
                    title=(hit.get("title", "") or "")[:80],
                )
                continue
            sources.append(
                {
                    "url": url,
                    "title": hit.get("title", ""),
                    "snippet": snippet,
                    "text": text,
                    "_relevance": relevance,
                    "_credibility": owner.credibility.get(url),
                    "_credibility_label": owner.credibility.get_label(url),
                    "_low_credibility_domain": is_low_credibility,
                }
            )
            self._log(
                "research_source_accepted",
                round=round_idx,
                url=url,
                title=(hit.get("title", "") or "")[:80],
                relevance=round(relevance, 2),
                credibility=round(owner.credibility.get(url), 2),
                credibility_label=owner.credibility.get_label(url),
                low_credibility_domain=is_low_credibility,
            )

        filter_dead_urls = compatibility._filter_dead_urls
        if filter_dead_urls is not None and sources:
            alive_urls, dead_urls = filter_dead_urls(
                [source["url"] for source in sources],
                timeout=5.0,
                max_workers=5,
                session_logger=owner.session_logger,
            )
            if dead_urls:
                alive_set = set(alive_urls)
                before = len(sources)
                sources = [source for source in sources if source["url"] in alive_set]
                self._log(
                    "research_dead_urls_filtered",
                    round=round_idx,
                    checked=before,
                    alive=len(alive_urls),
                    dead=len(dead_urls),
                    dead_urls=[
                        {"url": url, "reason": reason} for url, reason in dead_urls[:10]
                    ],
                )
        return sources

    def search_with_source_policy(
        self,
        query: str,
        round_idx: int,
        topic: str,
        source_allowlist: list[str],
        source_denylist: list[str],
    ) -> list[dict[str, Any]]:
        if not source_allowlist:
            return self.owner._search_round(
                query,
                round_idx,
                topic=topic,
                source_denylist=source_denylist,
            )
        sources: list[dict[str, Any]] = []
        for domain in source_allowlist:
            sources.extend(
                self.owner._search_round(
                    f"{query} site:{domain}",
                    round_idx,
                    topic=topic,
                    source_allowlist=source_allowlist,
                    source_denylist=source_denylist,
                )
            )
        return sources
