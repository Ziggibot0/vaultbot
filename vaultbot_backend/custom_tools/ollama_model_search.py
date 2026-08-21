"""
Agent-authored tool: ollama_model_search
"""

SCHEMA = {
    "name": "ollama_model_search",
    "description": (
        "Search Ollama's model library (https://ollama.com/search), list "
        "available tags for a model, list installed models, or pull a model. "
        "Actions: 'search' (query the web library), 'tags' (get available "
        "tags for a model), 'installed' (list locally installed models), "
        "'pull' (pull a model via ollama pull)."
    ),
    "parameters": {
        "properties": {
            "action": {
                "description": "One of: search, tags, installed, pull",
                "type": "string",
            },
            "category": {
                "description": (
                    "Category filter for search (e.g. vision, tools, "
                    "embedding, reasoning). Optional."
                ),
                "type": "string",
            },
            "query": {
                "description": (
                    "Search query (for action=search) or model name (for "
                    "action=tags or action=pull)"
                ),
                "type": "string",
            },
            "tag": {
                "description": (
                    "Specific tag to pull (for action=pull). If omitted, pulls :latest."
                ),
                "type": "string",
            },
        },
        "required": ["action"],
        "type": "object",
    },
}

import re  # noqa: E402
import subprocess  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402


def _fetch_url(url: str, timeout: int = 20) -> str:
    """Fetch a URL and return HTML text."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_search_results(html: str) -> list:
    """Parse Ollama search page HTML into model cards."""
    models = []
    # Model cards are in <li> elements with model links
    # Pattern: <a href="/library/<model>" ...> ... title="<model>" ...
    # <p ...>description</p> ... updated ... </a>
    blocks = re.split(r'<li\s+class="flex items-baseline', html)
    for block in blocks[1:]:  # skip first (before first <li>)
        # Extract model name from href
        href_match = re.search(r'href="/library/([^"]+)"', block)
        if not href_match:
            continue
        model_name = href_match.group(1)

        # Extract description
        desc_match = re.search(
            r'<p\s+class="max-w-lg[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL
        )
        description = ""
        if desc_match:
            description = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip()

        # Extract updated date
        updated_match = re.search(r"Updated.*?<span\s*>(.*?)</span>", block, re.DOTALL)
        updated = ""
        if updated_match:
            updated = re.sub(r"<[^>]+>", "", updated_match.group(1)).strip()

        # Extract pull count if available
        pulls_match = re.search(r"([\d,]+)\s*Pulls", block)
        pulls = pulls_match.group(1) if pulls_match else ""

        # Extract tag/count info
        tags = re.findall(r'<span\s+class="[^"]*rounded[^"]*"[^>]*>(.*?)</span>', block)
        tags = [re.sub(r"<[^>]+>", "", t).strip() for t in tags]

        models.append(
            {
                "name": model_name,
                "description": description[:200],
                "updated": updated,
                "pulls": pulls,
                "tags": tags,
            }
        )
    return models


def _parse_model_tags(html: str) -> list:
    """Parse a model's tags page HTML to extract available tags."""
    tags = set()
    # Pattern: href="/library/<model>:<tag>"
    for m in re.finditer(r'href="/library/[^:]+:([^"]+)"', html):
        tag = m.group(1)
        # Skip "View all" type links
        if not tag.startswith("View") and "&#" not in tag:
            tags.add(tag)
    return sorted(tags)


def _parse_installed() -> list:
    """Parse `ollama list` output."""
    from subprocess_utils import scrubbed_env

    result = subprocess.run(
        ["ollama", "list"],
        capture_output=True,
        timeout=10,
        env={**scrubbed_env(), "PYTHONIOENCODING": "utf-8"},
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    lines = stdout.strip().split("\n")
    if len(lines) < 2:
        return []

    models = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 4:
            name = parts[0]
            size = parts[2] if len(parts) > 2 else ""
            models.append({"name": name, "size": size})
    return models


def _pull_model(model: str, tag: str = "") -> dict:
    """Pull a model via ollama pull."""
    full_name = f"{model}:{{tag}}" if tag else model
    # Actually format properly
    full_name = f"{model}:{tag}" if tag else model

    from subprocess_utils import scrubbed_env

    result = subprocess.run(
        ["ollama", "pull", full_name],
        capture_output=True,
        timeout=600,
        env={**scrubbed_env(), "PYTHONIOENCODING": "utf-8"},
    )

    # Clean ANSI escape codes and Braille progress chars from output
    def clean(s: str) -> str:
        s = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", s)
        s = re.sub(r"[\u2800-\u28ff]", "", s)  # Braille patterns
        # Collapse repeated whitespace
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()

    stdout = clean(result.stdout.decode("utf-8", errors="replace"))
    stderr = clean(result.stderr.decode("utf-8", errors="replace"))

    return {
        "status": "ok" if result.returncode == 0 else "error",
        "model": full_name,
        "returncode": result.returncode,
        "stdout": stdout[-500:] if stdout else "",
        "stderr": stderr[-500:] if stderr else "",
    }


def run(args: dict) -> dict:
    action = args.get("action", "search")

    if action == "search":
        query = args.get("query", "")
        category = args.get("category", "")
        params = []
        if query:
            params.append(f"q={urllib.parse.quote(query)}")
        if category:
            params.append(f"c={urllib.parse.quote(category)}")
        url = "https://ollama.com/search"
        if params:
            url += "?" + "&".join(params)

        try:
            html = _fetch_url(url)
            models = _parse_search_results(html)
            return {"status": "ok", "url": url, "count": len(models), "models": models}
        except Exception as e:  # noqa: BLE001 — best-effort: returns error to caller
            return {"status": "error", "message": str(e)}

    elif action == "tags":
        model = args.get("query", "") or args.get("model", "")
        if not model:
            return {
                "status": "error",
                "message": "Model name required (pass via 'query' or 'model')",
            }
        url = f"https://ollama.com/library/{model}/tags"
        try:
            html = _fetch_url(url)
            tags = _parse_model_tags(html)
            return {
                "status": "ok",
                "model": model,
                "url": url,
                "tags": tags,
                "count": len(tags),
            }
        except Exception as e:  # noqa: BLE001 — best-effort: returns error to caller
            return {"status": "error", "message": str(e)}

    elif action == "installed":
        try:
            models = _parse_installed()
            return {"status": "ok", "count": len(models), "models": models}
        except Exception as e:  # noqa: BLE001 — best-effort: returns error to caller
            return {"status": "error", "message": str(e)}

    elif action == "pull":
        model = args.get("query", "") or args.get("model", "")
        tag = args.get("tag", "")
        if not model:
            return {
                "status": "error",
                "message": "Model name required (pass via 'query' or 'model')",
            }
        return _pull_model(model, tag)

    else:
        return {
            "status": "error",
            "message": f"Unknown action: {action}. Use: search, tags, installed, pull",
        }
