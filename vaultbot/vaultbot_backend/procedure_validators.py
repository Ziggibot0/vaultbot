"""Procedure step validation and condition evaluation.

Pure-logic helpers extracted from ``step_gate_runtime.py`` so the
main orchestrator stays focused on the execute loop.  Nothing in this
module touches the network, the LLM, or the filesystem — it is safe to
unit-test in isolation.

Two concerns live here:

1. **Validation** — decides whether a step's output satisfies its
   ``[validate: ...]`` criteria.  Three structured predicate forms are
   recognised (``at_least N <unit>``, ``contains "literal"``,
   ``matches /regex/``); anything else falls back to a deterministic
   word-overlap heuristic.

2. **Condition evaluation** — decides whether a step's
   ``[condition: ...]`` precondition holds so the runtime can skip the
   step when it doesn't.  Three recurrent forms are recognised (count
   comparison, presence, boolean status); unparseable conditions skip
   loudly (fail-safe).

``_count_thing`` is shared by both concerns (the ``at_least`` validator
and the count-comparison condition both call it), so it lives here and
is re-exported by ``step_gate_runtime`` for backward compatibility.

See:
  - ``step_gate_runtime.py`` — the orchestrator that calls these
  - ``procedure_compiler.py`` — where ``[validate:]`` / ``[condition:]``
    annotations are parsed off the step text
  - [[Procedure-Subprocess-Architecture]]
"""

from __future__ import annotations

import json
import re


# ── Stop words for validation (text steps) ──────────────────────────────

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "should",
        "could",
        "may",
        "might",
        "must",
        "can",
        "shall",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "about",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "from",
        "up",
        "down",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "or",
        "but",
        "if",
        "than",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "your",
        "our",
        "their",
        "his",
        "her",
        "my",
        "me",
        "you",
        "he",
        "she",
        "they",
        "we",
        "them",
        "us",
        "i",
        "him",
        "output",
        "contain",
        "include",
        "mention",
        "least",
        "more",
        "most",
        "some",
        "any",
        "all",
        "each",
        "every",
        "not",
        "no",
        "nor",
        "so",
        "too",
        "very",
        "just",
        "only",
        "also",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "own",
        "words",
    }
)


# ── Validation (for text steps) ──────────────────────────────────────────

# Structured validation predicates (Phase 4).  Three opt-in forms are
# recognised; free-text validation falls back to the word-overlap
# heuristic below.  See [[Procedure-Subprocess-Architecture]].
#
#   at_least N <unit>     count <unit> in output, compare >= N
#   contains "literal"   substring check
#   matches /regex/      regex search
#
# Units mirror ``_count_thing`` in the condition evaluator: notes/titles
# (wikilinks), sources/urls/links (http(s)), items/lines (bullets), or
# generic tokens for anything else.
_AT_LEAST_RE = re.compile(r"at_least\s+(\d+)\s*(?P<unit>\w+)?", re.IGNORECASE)
_VCONTAINS_RE = re.compile(r'contains\s+(?P<q>["\'])(?P<lit>.*?)(?P=q)', re.IGNORECASE)
_VMATCHES_RE = re.compile(r"matches\s+/(?P<pattern>.*)/", re.IGNORECASE)


def _parse_validation(text: str) -> dict | None:
    """Parse a validation string into a structured predicate, or None.

    Returns one of:
    - {"form": "at_least", "n": int, "unit": str|None}
    - {"form": "contains", "literal": str}
    - {"form": "matches", "pattern": str}
    - None (free-text → use the word-overlap fallback)
    """
    t = text.strip()
    m = _AT_LEAST_RE.search(t)
    if m:
        return {"form": "at_least", "n": int(m.group(1)), "unit": m.group("unit")}
    m = _VCONTAINS_RE.search(t)
    if m:
        return {"form": "contains", "literal": m.group("lit")}
    m = _VMATCHES_RE.search(t)
    if m:
        return {"form": "matches", "pattern": m.group("pattern")}
    return None


def _validate_word_overlap(
    output: str, validation: str | None
) -> tuple[bool, str | None]:
    """Deterministic validation using word-overlap heuristic.

    Extracts content words from the validation criteria (filtering
    stop words) and checks what fraction appear in the output.
    Passes if >= 50% of content words are found (case-insensitive).
    If no content words can be extracted, always passes.
    """
    if validation is None:
        return True, None

    words = re.findall(r"[a-zA-Z]+", validation.lower())
    content_words = [w for w in words if w not in _STOP_WORDS and len(w) > 1]

    if not content_words:
        return True, None

    output_lower = output.lower()
    found = sum(1 for w in content_words if w in output_lower)
    coverage = found / len(content_words)

    if coverage >= 0.5:
        return True, None

    missing = [w for w in content_words if w not in output_lower]
    return False, f"Validation terms not found in output: {', '.join(missing[:5])}"


def _validate_structured(output: str, validation: str) -> tuple[bool, str | None]:
    """Run a structured validation predicate. Returns (passed, error).

    Falls back to word-overlap if the string can't be parsed into a
    known form — backward-compatible with existing free-text validation.
    """
    pred = _parse_validation(validation)
    if pred is None:
        return _validate_word_overlap(output, validation)

    form = pred["form"]
    if form == "at_least":
        got = _count_thing(output, pred.get("unit") or "")
        if got >= pred["n"]:
            return True, None
        return (
            False,
            f"at_least {pred['n']} {pred.get('unit') or ''}: found {got}".strip(),
        )
    if form == "contains":
        lit = pred["literal"]
        if lit in output:
            return True, None
        return False, f"contains {lit!r}: not found"
    if form == "matches":
        try:
            if re.search(pred["pattern"], output):
                return True, None
            return False, f"matches /{pred['pattern']}/: no match"
        except re.error as e:
            return False, f"matches: invalid regex /{pred['pattern']}/: {e}"
    return _validate_word_overlap(output, validation)


def _validate_step(output: str, validation: str | None) -> tuple[bool, str | None]:
    """Dispatch validation: structured predicates first, word-overlap fallback."""
    if validation is None:
        return True, None
    return _validate_structured(output, validation)


# ── Counting helper (shared by validation + condition evaluation) ───────


def _count_thing(output: str, unit: str) -> int:
    """Count occurrences of a ``unit`` class in ``output``.

    Recognised units (case-insensitive):
    - notes / note / titles / title → count ``[[...]]`` wikilinks
    - sources / source / urls / url / links / link → count http(s) URLs
    - items / item / lines / line → count non-empty bullet/numbered lines

    Any unrecognised unit falls back to counting whitespace-separated
    tokens (a generic "things" count).  This keeps the predicate usable
    for ad-hoc units without raising.
    """
    u = (unit or "").lower().rstrip("s")  # normalise plural
    if u in {"note", "title"}:
        return len(re.findall(r"\[\[([^\]]+)\]\]", output))
    if u in {"source", "url", "link"}:
        return len(re.findall(r"https?://\S+", output))
    if u in {"item", "line"}:
        return sum(
            1
            for ln in output.split("\n")
            if ln.strip() and re.match(r"\s*([-*]|\d+[.)])\s+", ln)
        )
    # Fallback: count non-empty whitespace tokens.
    return len([t for t in output.split() if t])


# ── Condition evaluation (free-text predicates) ─────────────────────────
#
# Conditions are free text (e.g. ``[condition: if < 3 notes]``) but the
# vault uses three recurrent forms.  We evaluate those deterministically;
# anything unparseable is treated as "skip the step" (fail-safe: a
# precondition we can't verify must not let the step run).

# Count comparisons: "< 3 notes", ">= 2 titles", "!= 0 errors"
_COUNT_RE = re.compile(
    r"(?P<op><=|>=|==|!=|<|>)\s*(?P<n>\d+)\s*(?P<unit>\w+)?",
    re.IGNORECASE,
)
# Presence: 'contains "literal"' / "contains 'literal'"
_CONTAINS_RE = re.compile(
    r'contains\s+(?P<q>["\'])(?P<lit>.*?)(?P=q)',
    re.IGNORECASE,
)
# Boolean status: "passed" / "failed"
_BOOL_RE = re.compile(r"^(passed|failed)$", re.IGNORECASE)


def _evaluate_condition(
    condition: str,
    prior_results: dict[float, str],
    step_outputs: list[tuple[float, str]],
) -> tuple[bool, str]:
    """Evaluate a free-text condition predicate deterministically.

    Returns ``(should_run, reason)``.  ``should_run=False`` means the
    step must be skipped (its precondition did not hold, or could not be
    parsed — fail-safe skip).  ``reason`` is a short diagnostic logged
    to the session logger.

    Recognised forms (case-insensitive):
    1. **Count comparison**: ``< 3 notes``, ``>= 2 titles``, ``!= 0 errors``
       — compares ``_count_thing`` of the concatenated prior outputs
       against the integer.
    2. **Presence**: ``contains "literal"`` — substring check against
       the concatenated prior outputs.
    3. **Boolean status**: ``passed`` / ``failed`` — true if the last
       prior step passed (resp. failed).

    Any other form → ``(False, "unparseable")`` so the step is skipped
    loudly rather than run with an unverified precondition.
    """
    cond = condition.strip().lower()

    # Strip a leading "if " if present (common in vault notes).
    if cond.startswith("if "):
        cond = cond[3:].strip()

    joined = (
        "\n".join(str(o) for _, o in step_outputs)
        + "\n"
        + json.dumps(prior_results, default=str)
    )

    m = _COUNT_RE.search(cond)
    if m:
        op, n, unit = m.group("op"), int(m.group("n")), m.group("unit")
        got = _count_thing(joined, unit or "")
        checks = {
            "<": got < n,
            "<=": got <= n,
            ">": got > n,
            ">=": got >= n,
            "==": got == n,
            "!=": got != n,
        }
        ok = checks.get(op, False)
        return ok, f"count {got} {op} {n} {unit or ''}".strip()

    m = _CONTAINS_RE.search(cond)
    if m:
        lit = m.group("lit")
        return (lit in joined), f"contains {lit!r}"

    m = _BOOL_RE.match(cond)
    if m:
        want = m.group(1).lower()
        if not step_outputs:
            return (want == "failed"), f"bool {want} (no prior steps)"
        # The last step's pass/fail isn't directly available here; we
        # approximate by checking the last entry in prior_results has
        # content (treat as passed) — callers that need precise
        # pass/fail should use a count predicate instead.
        last_out = step_outputs[-1][1]
        ok = (want == "passed") if last_out else (want == "failed")
        return ok, f"bool {want}"

    return False, "unparseable"
