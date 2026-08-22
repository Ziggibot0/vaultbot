"""Single chokepoint that translates raw exceptions into typed ``Diagnosis``.

This is the *only* place in the backend that inspects exception types and
messages. Everywhere else just calls ``classify_error(exc, context)`` and
emits the result. Keeping the translation in one registry means:

  1. "What can the user see?" is a grep over ``_REGISTRY`` (this file).
  2. Adding a new user-facing failure kind = one entry here + one
     ``ProblemCategory`` member. No scatter-shot string matching across
     routers / chat loop / install scripts.
  3. Predicates are pure functions of ``(exc, context)`` → easy to unit
     test in isolation (see ``tests/test_diagnostics.py``).

A predicate returns ``True`` if it recognizes the exception; the first
match wins (order matters — put the most specific predicates first). If
nothing matches, the ``generic`` fallback produces a BROKEN diagnosis
with the raw repr tucked into ``raw_for_log`` only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from error_types import (
    AgentSilentError,
    Diagnosis,
    ProblemCategory,
    Severity,
    make_diagnosis,
)

# ─────────────────────────────────────────────────────────────────────────
# Context keys understood by predicates
# ─────────────────────────────────────────────────────────────────────────
# Callers may pass an optional ``context`` dict. Recognized keys:
#   stage         str   where this happened ("chat", "research", "startup"…)
#   http_status   int   an HTTP status code, if known
#   endpoint      str   the URL or ollama path hit (e.g. "/api/generate")
#   port          int   the port that was occupied, for port_in_use
#   path          str   a filesystem path, for synced_folder
#   model         str   a model id involved in the failure
# Predicates should treat missing context keys gracefully (use .get).


# ─────────────────────────────────────────────────────────────────────────
# Predicate + factory type
# ─────────────────────────────────────────────────────────────────────────
# A predicate inspects the exception (+ optional context) and returns
# True/False. A factory builds the Diagnosis. Splitting the two keeps
# each predicate a one-liner and each factory a pure string builder.
Predicate = Callable[[BaseException, dict[str, Any]], bool]
Factory = Callable[[BaseException, dict[str, Any]], Diagnosis]


# ─────────────────────────────────────────────────────────────────────────
# Detection helpers
# ─────────────────────────────────────────────────────────────────────────
def _exc_text(exc: BaseException) -> str:
    """Lowercased combined type + message for substring matching.

    Combines the class name and str() so a predicate can match on either
    (e.g. "ConnectionRefusedError" in the type, or "connection refused"
    in the message from requests' wrapper).
    """
    return f"{type(exc).__name__} {exc}".lower()


def _is_conn_refused(exc: BaseException, ctx: dict[str, Any]) -> bool:
    """True if this is a connection-refused / can't-reach-endpoint error.

    Covers:
      - requests.exceptions.ConnectionError (the common Ollama-down case)
      - raw ConnectionRefusedError (sync stdlib)
      - httpx.ConnectError if a future backend uses httpx
    We match the wrapper names rather than isinstance so this file stays
    independent of which HTTP library raised (and so it never imports
    requests at module load — it only needs the class *name* as a string).
    """
    text = _exc_text(exc)
    return (
        "connectionrefusederror" in text
        or "connectionerror" in text
        or "max retries exceeded" in text  # requests' phrasing
        or "connecterror" in text  # httpx
        or "connection refused" in text
        or "connection aborted" in text
    )


def _is_model_not_found(exc: BaseException, ctx: dict[str, Any]) -> bool:
    """Ollama / OpenAI 'model not found' responses.

    Ollama returns 404 with body ``{"error":"model '...' not found, ...}``;
    OpenAI returns 404 ``{"error":{"message":"The model '...' does not
    exist"}}``. Both surface as HTTPError (or its requests variant). We
    match on the canonical phrase; the "not pulled locally" variant is
    distinguished by whether the model id is in the local list (see
    ``diagnose()`` in routers/system.py, which can tell the two apart).
    """
    text = _exc_text(exc)
    return (
        ("model" in text and "not found" in text)
        or ("model" in text and "does not exist" in text)
        or "no such model" in text
    )


def _is_port_in_use(exc: BaseException, ctx: dict[str, Any]) -> bool:
    """EADDRINUSE from uvicorn binding, or an OSError errno 48/98."""
    text = _exc_text(exc)
    return (
        "eaddrinuse" in text
        or "address already in use" in text
        or "errno 48" in text
        or "errno 98" in text
        or "already in use" in text
    )


def _is_faiss_abi(exc: BaseException, ctx: dict[str, Any]) -> bool:
    """numpy 2.x vs faiss-cpu ABI mismatch.

    Symptom: ``ImportError: numpy.core.multiarray failed to import`` or
    a faiss load raising about undefined symbols. Pinned in
    requirements.txt to faiss-cpu>=1.11, but a user who upgraded numpy
    separately still hits this.
    """
    text = _exc_text(exc)
    return (
        "faiss" in text
        or ("numpy" in text and "multiarray" in text)
        or "undefined symbol" in text
        or "numpy.core.size" in text
    )


def _is_synced_folder(exc: BaseException, ctx: dict[str, Any]) -> bool:
    """Only raised synthetically by /preflight; we match on message tag."""
    return "synced folder" in _exc_text(exc)


def _is_setup_incomplete(exc: BaseException, ctx: dict[str, Any]) -> bool:
    """Missing venv / backend code — raised by the preflight checks."""
    return "setup incomplete" in _exc_text(exc) or "venv" in _exc_text(exc)


def _is_context_category(exc: BaseException, ctx: dict[str, Any]) -> bool:
    """Check if the caller explicitly tagged the category via context.

    Several subsystem-failure categories (retrieval, compaction, drift,
    history, research, verification, maintenance) don't have distinctive
    exception signatures — they're ordinary RuntimeError/IOError/etc.
    Callers tag them by passing ``context={"category": "retrieval_broken"}``
    so the classification is unambiguous. This predicate matches any
    recognized category tag and the factory reads it from context.
    """
    tag = (ctx.get("category") or "").strip().lower()
    return tag in (
        "retrieval_broken",
        "compaction_broken",
        "drift_lost",
        "history_lost",
        "research_degraded",
        "verification_broken",
        "maintenance_broken",
        "agent_silent",
    )


def _is_agent_silent(exc: BaseException, ctx: dict[str, Any]) -> bool:
    """The agent loop ended a turn with no user-facing text.

    Matched on type (``AgentSilentError``) so the classification is
    unambiguous without string matching. This is the fail-loud path for
    the "stops in its tracks" contract violation.
    """
    return isinstance(exc, AgentSilentError)


def _is_speech_unavailable(exc: BaseException, ctx: dict[str, Any]) -> bool:
    """A configured TTS/STT provider's dependency is missing or broken.

    The default ``edge-tts`` provider needs the optional ``edge-tts``
    package. When it isn't installed the import inside ``speech.py``
    raises ``ModuleNotFoundError``/``ImportError``; the previous code
    only ``logger.warning``-ed and returned a bare error dict, so the
    user got no audio and no explanation. This predicate recognizes that
    import failure (and the context-tagged ``speech_unavailable`` tag
    callers can use for runtime relay/endpoint failures) so the factory
    can surface a FIXABLE remedy card (issue #182).
    """
    if (ctx.get("category") or "").strip().lower() == "speech_unavailable":
        return True
    text = _exc_text(exc)
    return ("edge_tts" in text or "edge-tts" in text) and (
        "importerror" in text or "modulenotfounderror" in text
    )


# ─────────────────────────────────────────────────────────────────────────
# Factories — build the user-facing message + remedy per category
# ─────────────────────────────────────────────────────────────────────────
def _f_ollama_down(exc, ctx) -> Diagnosis:
    stage = ctx.get("stage", "talking")
    endpoint = ctx.get("endpoint", "Ollama")
    return make_diagnosis(
        ProblemCategory.OLLAMA_DOWN,
        user_message=(
            f"VaultBot can't reach {endpoint} while {stage}. "
            "This almost always means the Ollama app isn't running."
        ),
        remedy_hint=(
            "Open the Ollama app (check for its icon in your system tray), "
            "then click Restart below."
        ),
        action="restart",
        raw_for_log=repr(exc),
    )


def _f_model_not_found(exc, ctx) -> Diagnosis:
    model = ctx.get("model") or ""
    model_part = f" ({model})" if model else ""
    # We can't always tell pulled-vs-missing from the exception alone; the
    # /diagnose endpoint disambiguates. Default to "not pulled" since
    # that's the common case (installer pulled a different model, user
    # renamed config, etc.).
    return make_diagnosis(
        ProblemCategory.MODEL_NOT_PULLED,
        user_message=(f"The AI model VaultBot needs{model_part} isn't downloaded yet."),
        remedy_hint=(
            "Click Download model below — VaultBot will pull it for you. "
            "This is a one-time download (~1-4 GB)."
        ),
        action="pull_model",
        raw_for_log=repr(exc),
    )


def _f_port_in_use(exc, ctx) -> Diagnosis:
    port = ctx.get("port", 8000)
    return make_diagnosis(
        ProblemCategory.PORT_IN_USE,
        user_message=(f"VaultBot's port ({port}) is already taken by another program."),
        remedy_hint=(
            "Click Restart below — VaultBot will stop the stale process "
            "and start fresh."
        ),
        action="restart",
        raw_for_log=repr(exc),
    )


def _f_faiss_abi(exc, ctx) -> Diagnosis:
    return make_diagnosis(
        ProblemCategory.FAISS_ABI,
        user_message=(
            "VaultBot's search index can't start because its math library "
            "(FAISS) and numpy don't match versions."
        ),
        remedy_hint=(
            "This needs a one-time fix: use the Repair button below to "
            "reinstall the matching libraries."
        ),
        action="repair_faiss",
        raw_for_log=repr(exc),
    )


def _f_synced_folder(exc, ctx) -> Diagnosis:
    path = ctx.get("path", "")
    path_part = f" ({path})" if path else ""
    return make_diagnosis(
        ProblemCategory.SYNCED_FOLDER,
        user_message=(
            f"Your vault{path_part} is inside a cloud-sync folder "
            "(OneDrive, Dropbox, or iCloud). VaultBot's database files "
            "can get corrupted when two devices sync them at once."
        ),
        remedy_hint=(
            "Move your vault out of the synced folder to a plain local "
            "folder like Documents/VaultBot, then re-open it in Obsidian."
        ),
        action="move_vault",
        raw_for_log=repr(exc),
    )


def _f_setup_incomplete(exc, ctx) -> Diagnosis:
    missing = ctx.get("missing", "the backend")
    return make_diagnosis(
        ProblemCategory.SETUP_INCOMPLETE,
        user_message=(f"VaultBot isn't fully set up yet — {missing} is missing."),
        remedy_hint=(
            "Click Finish setup below. It opens the installer, which "
            "picks up where it left off."
        ),
        action="finish_setup",
        raw_for_log=repr(exc),
    )


# ─────────────────────────────────────────────────────────────────────────
# Subsystem-failure factories (context-tagged categories)
# ─────────────────────────────────────────────────────────────────────────
# These are for failures that don't have a distinctive exception signature
# (e.g. retrieval broke, compaction failed). Callers tag the category via
# context={"category": "retrieval_broken"} so the classification is
# unambiguous. The factory reads the tag to pick the right Diagnosis.

_SUBSYSTEM_MESSAGES: dict[str, tuple[ProblemCategory, str, str, str]] = {
    # tag: (category, user_message, remedy_hint, action)
    "retrieval_broken": (
        ProblemCategory.RETRIEVAL_BROKEN,
        "I couldn't search your vault for this question. "
        "I'll answer from what I know, but it may not be grounded "
        "in your notes.",
        "Try restarting VaultBot — if the search index is corrupted, "
        "a restart rebuilds it.",
        "restart",
    ),
    "compaction_broken": (
        ProblemCategory.COMPACTION_BROKEN,
        "I couldn't compact our conversation history. The chat still "
        "works, but it may slow down if our conversation gets very long.",
        "Try restarting VaultBot.",
        "restart",
    ),
    "drift_lost": (
        ProblemCategory.DRIFT_LOST,
        "I lost track of which notes you found helpful in the past. "
        "Search results will use base relevance until you give new "
        "feedback.",
        "",
        "",
    ),
    "history_lost": (
        ProblemCategory.HISTORY_LOST,
        "I couldn't load our conversation history. I'm starting fresh — "
        "our previous chat is lost, but you can continue talking now.",
        "Check disk space and file permissions on the VaultBot folder.",
        "",
    ),
    "research_degraded": (
        ProblemCategory.RESEARCH_DEGRADED,
        "I researched this using a simpler method than usual. "
        "The results are less detailed but still grounded in real sources.",
        "",
        "",
    ),
    "verification_broken": (
        ProblemCategory.VERIFICATION_BROKEN,
        "I couldn't verify the claims in this research note. "
        "The note is marked as unverified — treat its claims with "
        "appropriate caution.",
        "",
        "",
    ),
    "maintenance_broken": (
        ProblemCategory.MAINTENANCE_BROKEN,
        "I couldn't maintain some vault organization (links or "
        "chat trail). Your notes are safe, but some connections "
        "between notes may be missing.",
        "",
        "",
    ),
    "agent_silent": (
        ProblemCategory.AGENT_SILENT,
        "I finished working but didn't produce a response to you. "
        "This is a bug — I should never go silent. Your message and "
        "any work I did are saved.",
        "Send your message again. If it keeps happening, use Copy "
        "for support and report that the agent went silent.",
        "",
    ),
}


def _f_subsystem(exc, ctx) -> Diagnosis:
    """Build a Diagnosis for a context-tagged subsystem failure."""
    tag = (ctx.get("category") or "").strip().lower()
    entry = _SUBSYSTEM_MESSAGES.get(tag)
    if entry is None:
        # Shouldn't happen (predicate guards this), but fall through to
        # generic rather than crashing.
        return _f_generic(exc, ctx)
    category, user_message, remedy_hint, action = entry
    return make_diagnosis(
        category,
        user_message=user_message,
        remedy_hint=remedy_hint,
        action=action,
        raw_for_log=repr(exc),
    )


def _f_agent_silent(exc, ctx) -> Diagnosis:
    """The agent loop ended a turn without a valid final answer.

    Two cases raise AgentSilentError:
    1. The model produced no text at all after a nudge (empty terminal turn).
    2. The model did tool work without creating a plan, then produced a
       text-only round. Without a plan the framework can't determine
       whether the work is done, so it refuses to accept the text as a
       final answer.
    Both are contract violations — the turn is not done until the user
    has a real answer.
    """
    return make_diagnosis(
        ProblemCategory.AGENT_SILENT,
        user_message=(
            "I stopped working before giving you a proper answer. "
            "This is a bug — I should never leave you without a response. "
            "Your message and any work I did are saved."
        ),
        remedy_hint=(
            "Send your message again. If it keeps happening, use Copy "
            "for support and report that the agent stopped early."
        ),
        action="",
        raw_for_log=repr(exc),
    )


def _f_speech_unavailable(exc, ctx) -> Diagnosis:
    """A configured TTS/STT provider can't run (missing dep or dead relay).

    The default ``edge-tts`` provider needs the optional ``edge-tts``
    package. Without it TTS silently produced no audio (issue #182);
    this factory surfaces a FIXABLE remedy card instead. The remedy hint
    adapts to the provider: for edge-tts it tells the user to install the
    package; for an OpenAI-compatible endpoint it suggests checking the
    provider's URL/key.
    """
    provider = ctx.get("provider", "")
    role = ctx.get("role", "speech")
    text = _exc_text(exc)
    if "edge_tts" in text or "edge-tts" in text or provider == "edge-tts":
        return make_diagnosis(
            ProblemCategory.SPEECH_UNAVAILABLE,
            user_message=(
                f"{role.upper()} isn't available — the free Edge TTS "
                "voice needs the 'edge-tts' package, which isn't installed."
            ),
            remedy_hint=(
                "Run: pip install edge-tts  (then restart VaultBot). "
                "Or pick a different voice in Settings → Speech Models."
            ),
            action="open_settings",
            raw_for_log=repr(exc),
        )
    # OpenAI-compatible or unknown provider: the endpoint/key is the
    # likely culprit.
    return make_diagnosis(
        ProblemCategory.SPEECH_UNAVAILABLE,
        user_message=(
            f"{role.upper()} isn't available — the configured speech "
            "provider couldn't be reached."
        ),
        remedy_hint=(
            "Check the provider's URL and API key in Settings → Speech "
            "Models, then restart VaultBot."
        ),
        action="open_settings",
        raw_for_log=repr(exc),
    )


def _f_generic(exc, ctx) -> Diagnosis:
    stage = ctx.get("stage", "")
    stage_part = f" while {stage}" if stage else ""
    return make_diagnosis(
        ProblemCategory.GENERIC,
        user_message=(
            f"Something went wrong{stage_part}. VaultBot kept your notes and chat safe."
        ),
        remedy_hint=(
            "Try Restart below. If it keeps happening, use Copy for "
            "support and send it to whoever helps you with VaultBot."
        ),
        action="restart",
        severity=Severity.BROKEN,
        raw_for_log=repr(exc),
    )


# ─────────────────────────────────────────────────────────────────────────
# The registry — ORDER MATTERS (first match wins)
# ─────────────────────────────────────────────────────────────────────────
# Most specific first. The generic factory is the terminal fallback and
# is *not* in this list; ``classify_error`` uses it when no predicate
# matches, so the registry can never accidentally "miss".
_REGISTRY: list[tuple[Predicate, Factory]] = [
    # Environmental / setup failures are unambiguous — match them first so
    # a faiss import error isn't misread as a generic startup crash.
    (_is_faiss_abi, _f_faiss_abi),
    (_is_port_in_use, _f_port_in_use),
    (_is_synced_folder, _f_synced_folder),
    (_is_setup_incomplete, _f_setup_incomplete),
    # Context-tagged subsystem failures: callers pass
    # context={"category": "retrieval_broken"} etc. Match early so the
    # tag takes priority over generic exception-type matching.
    (_is_context_category, _f_subsystem),
    # Agent-silent: the loop ended with no user-facing text. Match before
    # the generic fallback so the user sees the agent_silent card, not a
    # generic "something went wrong".
    (_is_agent_silent, _f_agent_silent),
    # Speech provider unavailable: a missing edge-tts import or a dead
    # relay must surface a remedy card, not silent no-audio (issue #182).
    # Matched before the generic fallback so the user sees the install
    # hint instead of "something went wrong".
    (_is_speech_unavailable, _f_speech_unavailable),
    # Model problems before ollama-down: a 404 model-not-found is a
    # ConnectionError-shaped HTTPError sometimes, and the model message
    # is more actionable than "Ollama is down" (which would be wrong).
    (_is_model_not_found, _f_model_not_found),
    (_is_conn_refused, _f_ollama_down),
]


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────
def classify_error(
    exc: BaseException,
    context: dict[str, Any] | None = None,
) -> Diagnosis:
    """Translate a raw exception into a typed, user-facing ``Diagnosis``.

    This is the single function every ``except`` block in the backend
    should call when the error can reach the user. It never raises — on
    any internal failure it falls back to a generic Diagnosis so the UI
    always gets *something* renderable.

    Args:
        exc: the caught exception. Its repr is stored in
            ``raw_for_log`` (never shown as primary UI).
        context: optional dict of hints. See module docstring for keys.

    Returns:
        A ``Diagnosis`` whose ``category`` is one of ``ProblemCategory``
        and whose ``user_message`` is plain English with no stack trace.
    """
    ctx = context or {}
    try:
        for predicate, factory in _REGISTRY:
            if predicate(exc, ctx):
                return factory(exc, ctx)
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        # A buggy predicate must never crash classify_error; fall through
        # to generic so the UI still renders. The broken predicate will
        # show up in tests (which assert specific categories).
        pass
    return _f_generic(exc, ctx)


def diagnose_from_message(message: str, **context: Any) -> Diagnosis:
    """Classify from a plain string instead of an exception.

    Useful for the install preflight and /diagnose checks, which detect
    problems proactively (not from a raised exception). Builds a synthetic
    Exception so predicates that read ``str(exc)`` still work.
    """
    return classify_error(Exception(message), context=dict(context))
