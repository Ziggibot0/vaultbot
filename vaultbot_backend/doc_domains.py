"""Canonical doc-domain map for external modules (issue #207, Gap 1).

The doc-source gate in ``safe_writer.safe_write`` requires any edit that
imports a non-VaultBot module to cite the official documentation it was
checked against. ``Check-API-Against-Docs`` maps each external module to
its authoritative docs domain so the research dig stays on official
sources only.

This module is the single source of truth for that map. The procedure
imports it (``from doc_domains import resolve_doc_domain``) instead of
hardcoding the list, so the map can grow without hand-editing the
procedure. Resolution order:

  1. **stdlib** — any name in ``sys.stdlib_module_names`` maps to
     ``docs.python.org`` automatically (no map entry needed).
  2. **known third-party** — the ``DOC_DOMAINS`` map below.
  3. **PyPI metadata fallback** — for an installed package not in the
     map, derive a docs URL from its distribution metadata
     (``Project-URL`` / ``Home-page``) when it points at a docs host.
  4. **None** — the safe default: the caller must verify manually.

Returning ``None`` is the correct *safe* behaviour — it refuses to guess a
docs domain for an unknown module rather than pointing the dig at a
non-authoritative source.
"""

from __future__ import annotations

import importlib.metadata
import sys
from urllib.parse import urlparse

# Third-party module -> canonical docs domain. stdlib modules are NOT
# listed here — they are auto-detected via ``sys.stdlib_module_names`` and
# always map to ``docs.python.org``. Keep this map to genuinely
# third-party packages only.
DOC_DOMAINS: dict[str, str] = {
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
}

# Import-name -> distribution-name overrides for packages whose PyPI
# distribution name differs from the import name. Used by the metadata
# fallback so ``importlib.metadata`` can find the right distribution.
_DIST_NAME_OVERRIDES: dict[str, str] = {
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "pydantic_settings": "pydantic-settings",
    "cv2": "opencv-python",
    "PIL": "pillow",
}

# Hosts we trust as "documentation" when deriving a domain from PyPI
# metadata. A Home-page/Project-URL pointing at one of these is treated as
# a docs domain; anything else (e.g. a bare GitHub repo) is rejected so we
# don't point the dig at a non-authoritative source.
_DOCS_HOST_SUFFIXES: tuple[str, ...] = (
    "readthedocs.io",
    "readthedocs.org",
    "docs.python.org",
    "pydata.org",
    "palletsprojects.com",
    "tiangolo.com",
    "python-requests.org",
    "python-httpx.org",
    "scikit-learn.org",
    "numpy.org",
    "matplotlib.org",
    "docs.scipy.org",
    "docs.sqlalchemy.org",
    "docs.aiohttp.org",
    "docs.pytest.org",
    "docs.djangoproject.com",
    "docs.pydantic.dev",
    "crummy.com",
    "pyyaml.org",
)


def _stdlib_names() -> frozenset[str]:
    """Return the set of stdlib module names (py3.10+)."""
    return getattr(sys, "stdlib_module_names", frozenset())


def _derive_from_metadata(module: str) -> str | None:
    """Best-effort: derive a docs domain from the installed package's
    PyPI metadata. Returns None when the package isn't installed or its
    metadata doesn't point at a trusted docs host."""
    dist_name = _DIST_NAME_OVERRIDES.get(module, module)
    try:
        meta = importlib.metadata.metadata(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return None

    # Prefer an explicit "Documentation" Project-URL, then Home-page.
    candidates: list[str] = []
    for value in meta.get_all("Project-URL", []):
        # value is "Label, URL"
        label, _, url = value.partition(",")
        if "documentation" in label.lower():
            candidates.append(url.strip())
    home_pages = meta.get_all("Home-page", [])
    if home_pages:
        candidates.append(home_pages[0].strip())

    for url in candidates:
        if not url:
            continue
        host = urlparse(url).netloc.lower()
        if any(host.endswith(suffix) for suffix in _DOCS_HOST_SUFFIXES):
            return host
    return None


def resolve_doc_domain(module: str) -> str | None:
    """Return the canonical docs domain for ``module``, or None if unknown.

    Resolution order: stdlib -> known third-party map -> PyPI metadata
    fallback -> None (verify manually).
    """
    if not module:
        return None
    if module in _stdlib_names():
        return "docs.python.org"
    if module in DOC_DOMAINS:
        return DOC_DOMAINS[module]
    return _derive_from_metadata(module)
