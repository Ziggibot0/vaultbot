"""SearXNG instance manager — starts and monitors a local SearXNG search instance.

Used by the research engine as one of several search backends. Manages the
SearXNG Docker container lifecycle and health checks.
"""

import logging
import time
from pathlib import Path
from typing import Any

import docker
import requests
from bs4 import BeautifulSoup

_logger = logging.getLogger(__name__)

# Path to the SearxNG settings file that enables JSON output. Mounting this
# into the container ensures the JSON API survives container recreation.
_SEARXNG_SETTINGS_PATH = Path(__file__).parent / "searxng_settings.yml"


class SearxngManager:
    def __init__(
        self,
        docker_image: str = "searxng/searxng",
        port: int = 8080,
        session_logger=None,
    ):
        self.docker_image = docker_image
        self.port = port
        self.container_name = "vaultbot_searxng"
        self.client = docker.from_env()
        self.session_logger = session_logger

    def _log_tool(
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
            tool="searxng",
            method=method,
            inputs=inputs,
            outputs=outputs,
            duration_ms=duration_ms,
            error=error,
        )

    def is_running(self) -> bool:
        """Check if the searxng container is running and the service is ready."""
        try:
            container = self.client.containers.get(self.container_name)
            if container.status == "running":
                # Try to connect to the service
                try:
                    response = requests.get(f"http://localhost:{self.port}", timeout=5)
                    return response.status_code == 200
                except:
                    return False
            else:
                return False
        except docker.errors.NotFound:
            return False

    def start(self):
        """Start the searxng container if not already running."""
        t0 = time.time()
        if self.is_running():
            self._log_tool(
                "start",
                {"action": "already_running"},
                duration_ms=(time.time() - t0) * 1000,
            )
            _logger.info("Searxng container is already running.")
            return

        # Remove any stopped container with the same name
        try:
            container = self.client.containers.get(self.container_name)
            container.remove()
        except docker.errors.NotFound:
            pass

        # Pull the image if not present
        try:
            self.client.images.get(self.docker_image)
        except docker.errors.ImageNotFound:
            _logger.info(f"Pulling Docker image: {self.docker_image}")
            self.client.images.pull(self.docker_image)

        # Run the container, mounting our settings file so JSON output is
        # enabled and survives container recreation.
        _logger.info(f"Starting searxng container on port {self.port}...")
        volumes = {}
        if _SEARXNG_SETTINGS_PATH.exists():
            volumes[str(_SEARXNG_SETTINGS_PATH)] = {
                "bind": "/etc/searxng/settings.yml",
                "mode": "ro",
            }
        try:
            container = self.client.containers.run(
                self.docker_image,
                name=self.container_name,
                ports={f"{self.port}/tcp": self.port},
                volumes=volumes,
                detach=True,
            )
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_tool(
                "start",
                {"action": "run_container"},
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )
            raise
        # Wait for the service to be ready
        for _ in range(30):  # 30 seconds timeout
            if self.is_running():
                self._log_tool(
                    "start", {"action": "ready"}, duration_ms=(time.time() - t0) * 1000
                )
                _logger.info("Searxng is ready.")
                return
            time.sleep(1)
        err = "Searxng container did not become ready in time."
        self._log_tool(
            "start",
            {"action": "wait_ready"},
            error=err,
            duration_ms=(time.time() - t0) * 1000,
        )
        raise RuntimeError(err)

    def ensure_running(self):
        """Start the searxng container if not already running.

        Self-heals at every layer: starts Docker Desktop if the daemon is
        down, pulls the image if missing, recreates the container if it has
        stale settings, and waits for the service to be ready. The operator
        never has to touch Docker — VaultBot manages its own container.
        """
        if not self._ensure_daemon():
            raise RuntimeError("Docker daemon unavailable — SearXNG disabled")
        if self.is_running():
            # Health check: does the running container have our tuned settings?
            if _SEARXNG_SETTINGS_PATH.exists():
                try:
                    res = self.client.containers.get(self.container_name).exec_run(
                        ["grep", "-c", "outgoing", "/etc/searxng/settings.yml"]
                    )
                    has_tuning = res.exit_code == 0 and res.output.decode(
                        "utf-8", "ignore"
                    ).strip() not in ("", "0")
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    has_tuning = False
                if not has_tuning:
                    _logger.info(
                        "Searxng container has stale settings — recreating with tuned mount."
                    )
                    try:
                        self.client.containers.get(self.container_name).remove(
                            force=True
                        )
                    except docker.errors.NotFound:
                        pass
                    self.start()
        else:
            self.start()

    def _ensure_json_enabled(self):
        """If the running container lacks JSON format support, copy our
        settings file into it and restart it. This fixes containers that
        were started before the volume mount was added."""
        if not _SEARXNG_SETTINGS_PATH.exists():
            return
        try:
            # Test whether JSON search works already.
            resp = requests.get(
                f"http://localhost:{self.port}/search",
                params={"q": "test", "format": "json"},
                timeout=5,
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                return  # JSON already works.
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass
        # JSON failed — inject the settings and restart.
        try:
            container = self.client.containers.get(self.container_name)
            # Read the current settings to preserve the secret_key.
            try:
                old_settings = container.exec_run(["cat", "/etc/searxng/settings.yml"])
                if old_settings.exit_code == 0:
                    old_text = old_settings.output.decode("utf-8")
                    # Extract the existing secret_key to preserve it.
                    import re

                    m = re.search(r'secret_key:\s*"?([^"\n]+)"?', old_text)
                    if m:
                        secret = m.group(1).strip()
                        new_settings = _SEARXNG_SETTINGS_PATH.read_text(
                            encoding="utf-8"
                        )
                        new_settings = new_settings.replace(
                            "9Iu1NamztVUxXbMQWYLvWKMfNiZBsyit", secret
                        )
                        # Write via docker cp equivalent.
                        import tempfile

                        with tempfile.NamedTemporaryFile(
                            mode="w", suffix=".yml", delete=False, encoding="utf-8"
                        ) as tf:
                            tf.write(new_settings)
                        # Use the low-level API for put_archive.
                        import io
                        import tarfile

                        stream = io.BytesIO()
                        with tarfile.open(fileobj=stream, mode="w") as tar:
                            info = tarfile.TarInfo(name="settings.yml")
                            data = new_settings.encode("utf-8")
                            info.size = len(data)
                            tar.addfile(info, io.BytesIO(data))
                        stream.seek(0)
                        container.put_archive("/etc/searxng", stream)
                        container.restart()
                        time.sleep(3)
                        self._log_tool(
                            "ensure_json_enabled", {"action": "injected_and_restarted"}
                        )
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                self._log_tool("ensure_json_enabled", {"error": str(e)})
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_tool("ensure_json_enabled", {"error": str(e)})

    def search(self, query: str, timeout: int = 10) -> dict:
        """Perform a search using searxng and return the results as a dictionary.

        Uses the JSON API exclusively. If the JSON API fails, raises — no
        HTML-scraping fallback. The caller will see that search is broken.
        """
        self.ensure_running()
        t0 = time.time()
        try:
            response = requests.get(
                f"http://localhost:{self.port}/search",
                params={"q": query, "format": "json", "categories": "science,general"},
                timeout=timeout,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            self._log_tool(
                "search",
                {"query": query, "format": "json"},
                outputs={"result_count": len(data.get("results", []))},
                duration_ms=(time.time() - t0) * 1000,
            )
            return data
        except Exception as e:
            self._log_tool(
                "search",
                {"query": query, "format": "json"},
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )
            raise

    def scrape(self, url: str, timeout: int = 8) -> str:
        """Scrape a webpage directly and return clean markdown-ish text.

        Tries with realistic browser headers first; on 403, retries with an
        alternative user-agent. Sites that still block (JSTOR, ScienceDirect)
        return "" so the research engine falls back to the SearxNG snippet.
        """
        t0 = time.time()
        # Realistic browser header set — many sites 403 anything missing these.
        headerss = [
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            },
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            },
        ]
        last_err = None
        for hdr in headerss:
            try:
                response = requests.get(
                    url, headers=hdr, timeout=timeout, allow_redirects=True
                )
                response.raise_for_status()
                # Try to get readable text from HTML
                soup = BeautifulSoup(response.text, "lxml")
                # Remove script/style/nav/footer elements
                for tag in soup(
                    ["script", "style", "nav", "footer", "header", "aside", "form"]
                ):
                    tag.decompose()
                # Prefer article/main content
                main = soup.find("article") or soup.find("main") or soup.find("body")
                text = (
                    main.get_text(separator="\n", strip=True)
                    if main
                    else soup.get_text(separator="\n", strip=True)
                )
                # Collapse whitespace
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                text = "\n".join(lines)
                # Cap output size so downstream processing stays fast
                result = text[:4000]
                if len(result) < 80:
                    # Too short to be useful — try next UA.
                    last_err = "content too short"
                    continue
                self._log_tool(
                    "scrape",
                    {"url": url},
                    outputs={"text_length": len(result)},
                    duration_ms=(time.time() - t0) * 1000,
                )
                return result
            except requests.HTTPError as e:
                last_err = str(e)
                if e.response.status_code == 403:
                    continue  # try the next UA
                break  # non-403 error, don't retry
            except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                last_err = str(e)
                break
        self._log_tool(
            "scrape",
            {"url": url},
            error=last_err,
            duration_ms=(time.time() - t0) * 1000,
        )
        raise RuntimeError(f"Failed to scrape {url}: {last_err}")
