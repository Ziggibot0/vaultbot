"""Closed-set wikilink repair — fixes mangled citation stems (issue #335).

The model knows a note exists (it is in the per-turn allowed-citations set)
but mangles the exact stem — inserting or dropping spaces around hyphens:
``[[Chat- sup- homie]]`` instead of ``[[Chat-sup-homie]]``. Obsidian
wikilinks are filename-based, so the mangled link renders as "note doesn't
exist" and the grounding gate counts it as missing from the allowed set.

This module REPAIRS such links against the closed set before the answer is
scored or delivered. It is the chat-path sibling of
``research_synthesizer.repair_wikilinks`` (which fixes case and hallucinated
titles in research-note bodies) and follows the same
deterministic-no-LLM-no-IO contract as ``citation_gate``.

Design — deterministic ranked selection, NOT a keyword heuristic:
  1. build_alias_map: every allowed stem produces a few canonicalized aliases
     (lowercased; separators flattened). An exact alias hit is a certain
     repair — zero similarity doubt.
  2. If no alias hit, rank ALL allowed stems by difflib similarity against
     the mangled link and accept the best only if it clears
     Tunables.wikilink_repair_min_ratio (default 0.80) AND the length-gap
     guard. Deterministic tie-break: match_ratio, then shorter stem, then
     lexicographic.
A link that survives both steps with no repair is left untouched — the
grounding gate treats it as a genuine missing citation, which is correct
behavior for a fabricated title.

Pure leaf module: no I/O, no Services, no asyncio — same import-safety as
citation_gate (importable from chat_turn_finalize / citation_gate without
circular imports).
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

    ``allowed`` is the closed-set dict {stem: {"file_path", ...}} built by
    citation_gate.build_allowed_citations. Aliases of "Chat-sup-homie"
    include "chat-sup-homie" and separator-flattened forms like
    "chatsuphomie" — the shapes the model most plausibly mangles INTO.
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
    """Rank candidates by canonicalized difflib similarity; return the best.

    Deterministic tie-break: match_ratio, then shorter stem, then
    lexicographic. Returns None for an empty candidate list.
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

    min_ratio = float(getattr(TUNABLES, "wikilink_repair_min_ratio", 0.80))
    if ratio < min_ratio:
        return None
    if allowed is not None:
        # Never "repair" onto a stem that is not itself a real citation
        # target in the closed set (when one is provided).
        if stem not in allowed:
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

    Exact-existing stems return None (nothing to repair). A mangled link
    returns the best allowed stem that clears the similarity floor and the
    length-gap guard — deterministically — or None.
    """
    if not link or not allowed:
        return None
    probe = link.strip()
    if not probe or probe in allowed:
        return None
    # 1. Certain path: canonical alias hit.
    hit = build_alias_map(allowed).get(_canonical(probe))
    if hit is not None:
        return hit
    # 2. Ranked path: similarity against each allowed stem.
    return _rank(probe, list(allowed.keys()), allowed)


def repair_wikilinks_in_text(
    text: str,
    allowed: dict[str, dict[str, Any]],
) -> tuple[str, list[tuple[str, str]]]:
    """Repair mangled [[wikilinks]] in ``text`` against the closed set.

    Returns ``(repaired_text, repairs)`` where repairs is an ordered list
    of ``(original_target, repaired_stem)`` pairs — one per rewrite,
    including repeats. Links inside ```code fences``` are never touched
    (same rule as research_synthesizer.repair_wikilinks). Pipe aliases and
    #headings on a mangled target are preserved.

    An unrepairable link is left EXACTLY as written — the grounding gate
    needs to see genuine missing citations as missing.
    """
    if not text or not allowed:
        return text, []
    repairs: list[tuple[str, str]] = []

    def _fix(m: re.Match[str]) -> str:
        target = (m.group(1) or "").strip()
        heading = m.group(2) or ""
        alias = m.group(3)
        repaired = try_repair_stem(target, allowed)
        if repaired is None:
            return m.group(0)
        repairs.append((target, repaired))
        if alias is not None and alias.strip() and alias.strip() != target:
            return f"[[{repaired}{heading}|{alias.strip()}]]"
        return f"[[{repaired}{heading}]]"

    # Same fence-splitting rule as research_synthesizer.repair_wikilinks:
    # only edit even-indexed chunks (outside ``` fences).
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
    """``repair_wikilinks_in_text`` plus a graph-verified fallback tier.

    Tier 1: closed set (authoritative — the model was shown these).
    Tier 2: for links still unrepairable, rank ``candidate_provider(link)``
    stems by the same similarity rule and repair only the survivor that
    ALSO passes ``graph_lookup(candidate)``. ``graph_lookup`` receives the
    CANDIDATE stem (bool predicate — it verifies but cannot enumerate);
    ``candidate_provider`` returns candidate stems for a mangled link
    (list[str]) and may return [] — Tier 2 then does nothing for that
    link. If ``candidate_provider`` is None, Tier 2 is skipped entirely
    (Tier 1 already covers every note the model was shown).
    """
    repaired_text, repairs = repair_wikilinks_in_text(text, allowed)
    if not repaired_text or candidate_provider is None or not callable(
        candidate_provider
    ):
        return repaired_text, repairs
    remaining = [
        stem
        for stem in _iter_link_stems(repaired_text)
        if stem not in (allowed or {})
    ]
    if not remaining:
        return repaired_text, repairs
    _tier2: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in remaining:
        candidates = candidate_provider(link) or []
        if not candidates:
            continue
        candidate = _rank(link, candidates, None)
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
        if not verified:
            continue
        key = _canonical(link)
        if key in seen:
            continue
        seen.add(key)
        _tier2.append((link, candidate))
    if not _tier2:
        return repaired_text, repairs
    # Exactly one substitution site — reuse the same regex machinery.
    for mangled, corrected in _tier2:
        pattern = re.compile(r"\[\[\s*" + re.escape(mangled) + r"(\]|#|\|)")
        repaired_text = pattern.sub(
            "[[" + corrected + r"\1", repaired_text, count=1
        )
    return repaired_text, repairs + _tier2


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