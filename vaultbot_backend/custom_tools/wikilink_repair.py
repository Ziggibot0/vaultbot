"""Closed-set wikilink repair — fixes mangled citation stems (issue #335).

The model knows a note exists (it is in the per-turn allowed-citations set)
but mangles the exact stem — inserting or dropping spaces around hyphens:
``[[Chat- sup- homie]]`` instead of ``[[Chat-sup-homie]]``. Obsidian
wikilinks are filename-based, so the mangled link renders as "note doesn't
exist" and the grounding gate counts it as missing from the allowed set.

This module REPAIRS such links before the answer is scored or delivered.
Deterministic ranked selection, NOT a keyword heuristic:
  1. build_alias_map() — canonicalized aliases per stem (lowercased,
     separators flattened). An exact alias hit is a certain repair.
  2. difflib ranking with a TUNABLES similarity floor (default 0.80) and a
     length-gap guard. Deterministic tie-break: ratio, then shorter stem,
     then lexicographic.
An unrepairable link is left untouched — the grounding gate must still see
genuine fabrications as missing. Pure leaf: no I/O, no LLM, no asyncio.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(#[^\]|]*)?(?:\|([^\]]*))?\]\]")


def _canonical(token: str) -> str:
    """Fold case; treat spaces/hyphens/underscores as the same separator."""
    return re.sub(r"[\s_\-]+", "-", (token or "").strip().lower())


def build_alias_map(allowed: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Map every allowable lowercase alias of every stem to the real stem.

    ``allowed`` is the closed-set dict {stem: {...}} from
    citation_gate.build_allowed_citations. Aliases of "Chat-sup-homie"
    include "chat-sup-homie" and the flattened "chatsuphomie" — the shapes
    the model most plausibly mangles INTO.
    """
    out: dict[str, str] = {}
    for stem in allowed or {}:
        if not stem:
            continue
        canonical = _canonical(stem)
        out.setdefault(canonical, stem)
        out.setdefault(canonical.replace("-", ""), stem)
    return out


def _rank(
    probe: str,
    candidates: list[str],
    allowed: dict[str, dict[str, Any]] | None,
) -> str | None:
    """Rank candidates by canonicalized difflib ratio; return the best.

    Rejects below the TUNABLES.similarity floor, outside the closed set
    (when one is given), or past the length-gap guard. Tie-break:
    ratio, then shorter stem, then lexicographic.
    """
    best: tuple[float, str] | None = None
    for stem in candidates:
        ratio = difflib.SequenceMatcher(
            None, _canonical(probe), _canonical(stem)
        ).ratio()
        if (
            best is None
            or ratio > best[0]
            or (ratio == best[0] and (len(stem), stem) < (len(best[1]), best[1]))
        ):
            best = (ratio, stem)
    if best is None:
        return None
    ratio, stem = best
    from config import TUNABLES

    if ratio < float(getattr(TUNABLES, "wikilink_repair_min_ratio", 0.80)):
        return None
    if allowed is not None and stem not in allowed:
        return None
    max_gap = int(getattr(TUNABLES, "wikilink_repair_max_length_gap", 6))
    if len(probe) > len(stem) * 3 or len(stem) > max(
        len(probe) * 3, len(probe) + max_gap
    ):
        return None
    return stem


def try_repair_stem(
    link: str,
    allowed: dict[str, dict[str, Any]],
) -> str | None:
    """Return the corrected stem for ``link``, or None if no safe repair.

    Exact-existing stems return None (nothing to repair).
    """
    if not link or not allowed:
        return None
    probe = link.strip()
    if not probe or probe in allowed:
        return None
    hit = build_alias_map(allowed).get(_canonical(probe))
    if hit is not None:
        return hit
    return _rank(probe, list(allowed.keys()), allowed)


def repair_wikilinks_in_text(
    text: str,
    allowed: dict[str, dict[str, Any]],
) -> tuple[str, list[tuple[str, str]]]:
    """Repair mangled [[wikilinks]] in ``text`` against the closed set.

    Returns ``(repaired_text, repairs)``; repairs is ordered
    ``(original_target, repaired_stem)`` pairs. Links inside ```fence```
    blocks are never touched; pipe aliases and #headings are preserved.
    """
    if not text or not allowed:
        return text, []
    repairs: list[tuple[str, str]] = []

    def _fix(m: re.Match[str]) -> str:
        target = (m.group(1) or "").strip()
        repaired = try_repair_stem(target, allowed)
        if repaired is None:
            return m.group(0)
        repairs.append((target, repaired))
        alias = m.group(3)
        if alias is not None and alias.strip() and alias.strip() != target:
            return f"[[{repaired}{m.group(2) or ''}|{alias.strip()}]]"
        return f"[[{repaired}{m.group(2) or ''}]]"

    parts = text.split("```")
    for i in range(0, len(parts), 2):
        parts[i] = _WIKILINK_RE.sub(_fix, parts[i])
    return "```".join(parts), repairs


def repair_wikilinks_verified(
    text: str,
    allowed: dict[str, dict[str, Any]],
    graph_lookup,
    candidate_provider=None,
) -> tuple[str, list[tuple[str, str]]]:
    """Tier 1 (closed set) + Tier 2 (graph-verified fallback).

    Tier 2 ranks ``candidate_provider(link)`` stems and repairs the winner
    only if ``graph_lookup(candidate)`` confirms the note exists. With no
    provider (the common case), Tier 2 is skipped — Tier 1 already covers
    every note the model was shown.
    """
    repaired_text, repairs = repair_wikilinks_in_text(text, allowed)
    if candidate_provider is None or not callable(candidate_provider):
        return repaired_text, repairs
    remaining = [
        stem for stem in _iter_link_stems(repaired_text) if stem not in (allowed or {})
    ]
    if not remaining:
        return repaired_text, repairs
    tier2: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in remaining:
        candidate = _rank(link, candidate_provider(link) or [], None)
        if candidate is None or candidate in (allowed or {}):
            continue
        try:
            verified = bool(graph_lookup(candidate))
        except Exception as error:  # noqa: BLE001 — repair is best-effort
            from session_logger import session_logger

            session_logger.log(
                "wikilink_repair_tier2_lookup_failed", {"error": str(error)}
            )
            continue
        key = _canonical(link)
        if verified and key not in seen:
            seen.add(key)
            tier2.append((link, candidate))
    if not tier2:
        return repaired_text, repairs
    for mangled, corrected in tier2:
        pattern = re.compile(r"\[\[\s*" + re.escape(mangled) + r"(\]|#|\|)")
        repaired_text = pattern.sub("[[" + corrected + r"\1", repaired_text, count=1)
    return repaired_text, repairs + tier2


def _iter_link_stems(text: str) -> list[str]:
    """Return the raw link targets (stems) in order, deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _WIKILINK_RE.finditer(text or ""):
        stem = (m.group(1) or "").strip()
        if stem and stem not in seen:
            seen.add(stem)
            out.append(stem)
    return out
