"""Pure text→number transformations for the research engine.

No I/O, no state, no dependencies on the research engine. These functions
tokenize, split sentences, extract keyterms, and score text relevance
deterministically (no LLM).
"""

import math
import re
from collections import Counter

# --- Stopwords for keyterm extraction (no external dep) -------------------
_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "if",
    "then",
    "else",
    "when",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "with",
    "without",
    "into",
    "from",
    "by",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "they",
    "them",
    "their",
    "we",
    "you",
    "your",
    "our",
    "i",
    "me",
    "my",
    "he",
    "she",
    "his",
    "her",
    "him",
    "who",
    "whom",
    "which",
    "what",
    "why",
    "how",
    "where",
    "there",
    "here",
    "about",
    "than",
    "so",
    "do",
    "does",
    "did",
    "doing",
    "have",
    "has",
    "had",
    "not",
    "no",
    "yes",
    "can",
    "could",
    "should",
    "would",
    "may",
    "might",
    "will",
    "shall",
    "must",
    "between",
    "within",
    "among",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "over",
    "under",
    "up",
    "down",
    "out",
    "off",
    "again",
    "further",
    "once",
    "such",
    "also",
    "only",
    "own",
    "same",
    "other",
    "some",
    "any",
    "all",
    "both",
    "each",
    "few",
    "more",
    "most",
    "many",
    "much",
    "little",
    "less",
    "least",
    "vs",
    "versus",
    "via",
    "per",
    "etc",
    "ie",
    "eg",
    "upon",
    "because",
    "while",
    "since",
    "however",
    "therefore",
    "thus",
    "hence",
    "whether",
    "either",
    "neither",
}

# Broad generic tech vocabulary that appears in millions of unrelated pages.
# These are NOT signal on their own — a source mentioning only "python" and
# "index" tells you nothing about whether it's about FAISS. A source must pair
# them with a real signal term (FAISS, IndexIDMap2, remove_ids, …) to pass.
_GENERIC_TERMS = {
    "python",
    "index",
    "vector",
    "vectors",
    "array",
    "arrays",
    "data",
    "code",
    "function",
    "method",
    "class",
    "object",
    "value",
    "values",
    "list",
    "dict",
    "string",
    "file",
    "files",
    "system",
    "server",
    "client",
    "model",
    "models",
    "training",
    "learning",
    "search",
    "query",
    "database",
    "api",
    "config",
    "build",
    "run",
    "test",
    "error",
    "bug",
    "issue",
    "problem",
    "solution",
    "example",
    "tutorial",
    "guide",
    "library",
    "package",
    "module",
    "import",
    "install",
    "version",
    "performance",
    "memory",
    "time",
    "size",
    "type",
    "name",
    "key",
    "keys",
    "add",
    "delete",
    "remove",
    "update",
    "create",
    "read",
    "write",
    "load",
    "save",
    "open",
    "close",
    "start",
    "stop",
    "set",
    "get",
    "new",
    "old",
    "best",
    "good",
    "bad",
    "how",
    "what",
    "why",
    "when",
    "where",
    "without",
    "with",
    "from",
    "into",
    "using",
    "use",
    "used",
    "uses",
    "research",
    "study",
    "paper",
    "analysis",
    "results",
    "through",
    "group",
    "groups",
    "approach",
    "based",
    "proposed",
    "novel",
    "recent",
    "current",
}


def keyterms(text: str, max_terms: int = 6) -> list[str]:
    """Extract salient noun-ish keyterms without an LLM.

    Ranks tokens by frequency * length, filters stopwords, and keeps proper
    nouns (capitalized mid-sentence) and capitalized multi-word phrases.
    Also preserves site:domain.com search operators so the search backend
    can target specific domains (e.g., site:github.com for forum discussions).
    """
    # Extract site: operators BEFORE any preprocessing — the : character is
    # lost by the tokenizer, so we must capture them from the original text.
    site_operators = re.findall(r"\bsite:\S+", text, re.I)
    # Slug detection: the autonomous researcher passes vault note names (e.g.
    # ``FAISS-IndexIDMap2-remove_ids-vector-removal-API-documentation``) as
    # the topic. These use hyphens as word separators, not intra-token
    # punctuation. A topic with no spaces but with hyphens is treated as a
    # slug and split on hyphens BEFORE tokenizing, so each concept becomes its
    # own keyterm candidate instead of the whole slug collapsing into one
    # giant token like "how-to-safe_write".
    original = text
    if " " not in text.strip() and "-" in text and len(text) > 8:
        token_source = text.replace("-", " ")
    else:
        token_source = text
    # Pull capitalized noun phrases from the ORIGINAL-cased text BEFORE
    # lowercasing. The previous code lowercased first, so the [A-Z] regex
    # matched nothing and proper nouns like "FAISS IndexIDMap2" were never
    # extracted — generic tokens ("python", "vectors") then dominated and
    # arXiv returned any paper mentioning "Python".
    phrases = re.findall(
        r"\b([A-Z][a-zA-Z0-9_]+(?:\s+[A-Z][a-zA-Z0-9_]+){0,3})\b", original
    )
    # Pull quoted phrases (the user often telegraphs the topic).
    quoted = re.findall(r"[\"']([^\"']+)[\"']", original)
    work = token_source.replace("?", " ").replace("!", " ").strip().lower()
    # Tokenize the lowercased text — KEEP underscores so remove_ids stays
    # one token (was split into "remove"+"ids" by the old [a-z][a-z0-9\-]+
    # regex, losing the actual API name and matching generic "remove").
    tokens = re.findall(r"[a-z][a-z0-9_]+", work)
    # Score single tokens: freq * length, skip stopwords.
    scored: dict[str, float] = {}
    tok_counter = Counter(tokens)
    for tok, count in tok_counter.items():
        if tok in _STOPWORDS or len(tok) < 3:
            continue
        scored[tok] = count * (1 + math.log(len(tok)))
    # Merge: site: operators > quoted > phrases > single tokens.
    result: list[str] = []
    seen = set()
    # site: operators get highest priority — they're explicit user intent.
    for so in site_operators:
        so_low = so.lower()
        if so_low not in seen:
            result.append(so)
            seen.add(so_low)
    for q in quoted:
        ql = q.lower()
        if ql and ql not in seen:
            result.append(q.strip())
            seen.add(ql)
    for p in phrases:
        pl = p.lower()
        words = pl.split()
        # Skip if all words are stopwords.
        if any(w not in _STOPWORDS for w in words) and pl not in seen:
            result.append(p.strip())
            seen.add(pl)
    # Top single tokens — leave room for site: operators.
    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    for tok, _ in ranked:
        if tok in seen:
            continue
        result.append(tok)
        seen.add(tok)
        if len(result) >= max_terms:
            break
    return result[:max_terms]


def split_sentences(text: str) -> list[str]:
    """Cheap sentence splitter that's good enough for scraped web text."""
    text = re.sub(r"\s+", " ", text).strip()
    # Split on . ! ? followed by whitespace+capital, or newlines.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    sentences = []
    for p in parts:
        p = p.strip()
        if 30 <= len(p) <= 400:  # ignore headings/fragments/giant blobs
            sentences.append(p)
    return sentences


def tokenize_light(text: str) -> list[str]:
    return [
        w
        for w in re.findall(r"[a-z0-9][a-z0-9\-]+", text.lower())
        if w not in _STOPWORDS and len(w) > 2
    ]


def score_sentence(sentence: str, keyterms: list[str], source_count: int) -> float:
    """Extractive score: keyword density * corroboration boost.

    Signal terms (proper nouns, API names, multi-word phrases — the terms
    that actually disambiguate the topic) are weighted 5× higher than
    generic single-word keyterms. A sentence that mentions "FAISS" and
    "remove_ids" is almost certainly on-topic; one that only mentions
    "python" and "index" is not, even if those are in the keyterm list.
    """
    toks = tokenize_light(sentence)
    if not toks:
        return 0.0
    tok_set = set(toks)
    sig = signal_terms(keyterms)
    sig_set = {s.lower() for s in sig}
    hits = 0.0
    for kt in keyterms:
        kt_low = kt.lower()
        if " " in kt_low:
            if kt_low in sentence.lower():
                # Multi-word phrases are always signal → heavy weight.
                hits += 5.0
        elif kt_low in sig_set:
            if kt_low in tok_set:
                hits += 5.0
            elif kt_low in sentence.lower():
                hits += 2.0
        elif kt_low in tok_set:
            # Generic term — small weight, just density filler.
            hits += 1.0
    density = hits / math.sqrt(len(toks))
    # Corroboration: a fact supported by N independent sources is worth more.
    corroboration = 1.0 + 0.15 * max(0, source_count - 1)
    return density * corroboration


def signal_terms(keyterms: list[str]) -> list[str]:
    """Return the high-specificity subset of keyterms.

    These are the terms that actually disambiguate the topic from the
    millions of pages that share its generic vocabulary. Used as the
    relevance gate's yardstick: a source must contain at least one
    signal term (or, for very generic topics, a quorum of the keyterms).
    """
    sig: list[str] = []
    for kt in keyterms:
        low = kt.lower()
        if low.startswith("site:"):
            # site:github.com -> the domain is the signal.
            sig.append(low[5:])
            continue
        # Multi-word phrases are always signal (rare by definition).
        if " " in low:
            sig.append(low)
            continue
        # Underscored or hyphenated compound tokens are API/library names.
        if "_" in low or "-" in low:
            sig.append(low)
            continue
        # Capitalized single tokens (proper nouns) are signal. We can't see
        # case from keyterms (already lowered by keyterms), but tokens ≥5
        # chars that aren't in a broad generic-tech stoplist are treated as
        # signal. Short common words ("python", "index", "vector") are NOT
        # signal alone — they need a signal partner.
        if (len(low) >= 5 and low not in _GENERIC_TERMS) or (
            len(low) >= 2 and low == low.upper() and low.isalpha()
        ):
            sig.append(low)
    # Dedup preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for s in sig:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out
