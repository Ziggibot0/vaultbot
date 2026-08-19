"""URL liveness verification for the research engine.

Checks whether a URL actually resolves (returns a 2xx or 3xx response)
before accepting it as a research source. This prevents the research
engine from citing dead links — URLs that 404, timeout, or resolve to
a parking/error page.

The check is a lightweight HTTP HEAD (falls back to GET if HEAD is not
supported). It uses a short timeout (5s) and realistic browser headers
to avoid being blocked by anti-bot measures.

This module is intentionally separate from source_classification.py
(which is pure logic, no I/O) and free_search.py (which is the search
backend) — liveness verification is a post-search gate that runs after
the search engine returns candidate URLs.
"""

from __future__ import annotations

import threading

import requests

# Realistic browser headers — many sites 403 the python-requests default UA.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Status codes that indicate the URL is alive (resolves to real content).
# 2xx = success, 3xx = redirect (the page exists, just moved).
_LIVE_STATUS = set(range(200, 400))

# Status codes that indicate the URL is dead.
# 404 = not found, 410 = gone, 451 = unavailable for legal reasons.
_DEAD_STATUS = {404, 410, 451}

# Default timeout for liveness checks (seconds). Short — this runs
# inline during research and we don't want one slow server to block
# the whole pipeline.
_DEFAULT_TIMEOUT = 5.0


def check_url_alive(
    url: str,
    timeout: float = _DEFAULT_TIMEOUT,
    session_logger=None,
) -> tuple[bool, str]:
    """Check if a URL resolves to live content.

    Returns (is_alive, reason):
      - (True, "ok") if the URL returned a 2xx/3xx status.
      - (False, "status_404") if the URL returned a dead status code.
      - (False, "timeout") if the request timed out.
      - (False, "connection_error") if the connection failed (DNS, refused, etc.).
      - (False, "error: <details>") for other failures.

    Uses HEAD first (lightweight), falls back to GET with stream=True
    (read only headers) if the server rejects HEAD.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False, "invalid_url"

    try:
        # Try HEAD first — it's the cheapest check (no body transfer).
        resp = requests.head(
            url,
            headers=_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        status = resp.status_code
        # Some servers reject HEAD with 405 (Method Not Allowed) but
        # would serve a GET just fine. Fall back to GET.
        if status == 405:
            resp = requests.get(
                url,
                headers=_HEADERS,
                timeout=timeout,
                allow_redirects=True,
                stream=True,  # Don't download the body — just headers.
            )
            status = resp.status_code
            resp.close()
        if status in _LIVE_STATUS:
            return True, "ok"
        if status in _DEAD_STATUS:
            return False, f"status_{status}"
        # 401/403 might just be auth-gated content — the URL exists.
        # 5xx is server error, could be transient — treat as alive
        # (the scrape step will get the real content or fail there).
        if status in (401, 403) or status >= 500:
            return True, f"status_{status}_treated_alive"
        return False, f"status_{status}"
    except requests.exceptions.Timeout:
        return False, "timeout"
    except requests.exceptions.ConnectionError:
        # DNS failure, connection refused, etc.
        return False, "connection_error"
    except Exception as e:  # noqa: BLE001 — best-effort liveness check, returns (False, reason) to caller
        return False, f"error:{type(e).__name__}"


def filter_dead_urls(
    urls: list[str],
    timeout: float = _DEFAULT_TIMEOUT,
    max_workers: int = 5,
    session_logger=None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Check a batch of URLs for liveness in parallel.

    Returns (alive_urls, dead_urls_with_reasons).
    dead_urls_with_reasons is a list of (url, reason) tuples.

    Uses a thread pool to check URLs concurrently — each check is a
    network call with a timeout, and checking them serially would be
    too slow for a batch of 5-10 sources.
    """
    if not urls:
        return [], []

    alive: list[str] = []
    dead: list[tuple[str, str]] = []
    results: dict[str, tuple[bool, str]] = {}
    threads: list[threading.Thread] = []
    # Use a semaphore to limit concurrent requests.
    sem = threading.Semaphore(max_workers)

    def _check(u: str):
        with sem:
            results[u] = check_url_alive(
                u, timeout=timeout, session_logger=session_logger
            )

    for u in urls:
        th = threading.Thread(target=_check, args=(u,), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(timeout=timeout + 5)

    for u in urls:
        is_alive, reason = results.get(u, (False, "thread_timeout"))
        if is_alive:
            alive.append(u)
        else:
            dead.append((u, reason))

    if session_logger is not None:
        try:
            session_logger.log(
                "url_liveness_check",
                {
                    "total": len(urls),
                    "alive": len(alive),
                    "dead": len(dead),
                    "dead_details": [(u, r) for u, r in dead[:10]],
                },
            )
        except Exception:  # noqa: BLE001 — best-effort logging, liveness result still returned to caller
            pass

    return alive, dead
