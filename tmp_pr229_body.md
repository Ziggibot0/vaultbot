### Problem
code_run executes LLM-authored Python in a subprocess to test code before
adopting it. The read-only guard (issue #207) blocks file writes, but two
things were still not enforced:
- **Full network egress** — a malicious model could `requests.post(exfil_url, ...)` or `socket`/`urllib` to exfiltrate anything it could read.
- **Unfettered reads of secret files** — `scrubbed_env()` strips secret-named env vars but NOT secret files on disk. code_run could `open('.env')`, `Path('providers.json').read_text()`, `*_tokens.json`, `*_config.json`.

### Fix
Extend the code_run guard preamble (issue #207) to:
- **Block network imports** — `socket`, `ssl`, `http`, `urllib`, `requests`, `httpx`, `aiohttp`, `websocket(s)`, `urllib3`, `wsgiref`, `smtplib`, `ftplib`, blocking the import path (so `import urllib.request` is caught too). Raises `PermissionError` with a clear message; plain stdlib imports keep working.
- **Block reads of secret/credential files** — scoped to the repo root (via `build_guard_preamble(repo_root)`), so a test reading an unrelated `config.json` outside the repo is not falsely blocked. `.env` is always protected regardless of location. Covers `open()`, `os.open()`, `Path.open()`, `Path.read_text()`, `Path.read_bytes()`.

Repo root is threaded through `self_improver.code_run`. The guard is opt-out (`allow_write=True` skips the preamble) — consistent with the existing read-only guard.

### Tests
16 tests (issue #207 + #229): network import blocking (requests/socket/urllib/http.client), plain-import pass-through, secret-file read blocking (.env/providers.json/_tokens.json/_config.json), repo-root scoping (unrelated JSON outside repo still readable), and `.env` always-protected.

### Residual risk
Defense-in-depth, not a true OS sandbox (see SECURITY.md). A determined attacker could still reach lower-level primitives; a container / Job Object sandbox is the roadmap hardening item. The raised bar catches the accidental/opportunistic bypass.
