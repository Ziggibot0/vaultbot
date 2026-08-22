"""Typed error model for VaultBot's human-facing error surface.

This module defines the *vocabulary* of failures a non-technical user can
see. It is deliberately tiny and dependency-free so it can be imported
anywhere (routers, chat loop, install preflight) without pulling in
FastAPI, services, or the LLM stack.

Design principle (for contributors):
    Errors are classified **once, at the edge** (see ``diagnostics.py``).
    Every exception that can reach the user is converted into a
    ``Diagnosis`` here. The frontend renders a ``Diagnosis`` — it never
    renders a raw string. This is what makes "the average user never sees
    a stack trace" an enforced rule rather than a hope.

See: diagnostics.py for the ``classify_error`` translation chokepoint.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class AgentSilentError(RuntimeError):
    """Raised when the agent loop ends a turn with no user-facing text.

    This is a framework-detected contract violation, not a normal stop.
    The turn is not done until the user has a response. Raising this (vs
    shipping an empty answer_done) makes the failure loud: the outer
    handler classifies it via ``classify_error`` and the user sees a
    problem card instead of silence. The diagnostics registry matches
    this type specifically (see ``_is_agent_silent`` in diagnostics.py).
    """


class ProblemCategory(str, Enum):  # noqa: UP042 - str+Enum for JSON serialization on 3.11+
    """The closed set of user-facing failure kinds.

    Values are ``str`` so they serialize cleanly to JSON for the WebSocket
    ``problem`` event and the ``/diagnose`` endpoint. Add a new member
    here only when you also add a matching predicate in
    ``diagnostics.py`` — the two tables must stay in sync.

    Ordering is roughly by "how fixable without a developer":
        ollama_down / model_missing  → one user action (open app / pull)
        port_in_use / synced_folder → one VaultBot action (restart / move)
        faiss_abi                     → environmental, needs a reinstall
        config_conflict / generic     → least actionable
    """

    # Ollama isn't running (or the cloud LLM endpoint is unreachable).
    OLLAMA_DOWN = "ollama_down"
    # The configured model exists in the registry but isn't pulled locally.
    MODEL_NOT_PULLED = "model_not_pulled"
    # The configured model id is malformed / unknown to the backend.
    MODEL_MISSING = "model_missing"
    # Port 8000 (or configured port) is already occupied by another process.
    PORT_IN_USE = "port_in_use"
    # Vault lives inside a sync folder (OneDrive/Dropbox/iCloud) — data risk.
    SYNCED_FOLDER = "synced_folder"
    # FAISS / numpy ABI mismatch — needs a clean reinstall of faiss-cpu.
    FAISS_ABI = "faiss_abi"
    # A self-update failed partway; the backup exists and can be restored.
    UPDATE_PARTIAL = "update_partial"
    # Two config sources (plugin settings vs .env) disagree on a value.
    CONFIG_CONFLICT = "config_conflict"
    # The Python environment / backend code isn't installed yet.
    SETUP_INCOMPLETE = "setup_incomplete"
    # Fused retrieval (vector + graph + backlinks) couldn't search the vault.
    RETRIEVAL_BROKEN = "retrieval_broken"
    # Context compaction (summarizing old conversation) failed — context
    # may grow unbounded or the LLM summarizer is dead.
    COMPACTION_BROKEN = "compaction_broken"
    # Embedding drift state (user feedback on note helpfulness) was lost
    # or corrupted — retrieval reverts to pre-feedback quality.
    DRIFT_LOST = "drift_lost"
    # Conversation history file is corrupt — the agent starts fresh with
    # no memory of prior turns.
    HISTORY_LOST = "history_lost"
    # Research engine fell back to extractive synthesis (keyword scoring)
    # instead of LLM reasoning — research notes are lower quality.
    RESEARCH_DEGRADED = "research_degraded"
    # Claim verification couldn't run (source text unreachable or LLM
    # unavailable) — claims in research notes are unverified.
    VERIFICATION_BROKEN = "verification_broken"
    # Vault maintenance / weaving / trail tracking failed — vault
    # organization features (wikilinks, chat trail) stopped working.
    MAINTENANCE_BROKEN = "maintenance_broken"
    # The agent loop ended a turn without producing any user-facing text.
    # This is a framework-detected contract violation ("the turn isn't
    # done until the user has text"), not a normal stop. Fail-loud: the
    # user sees a problem card, never silence.
    AGENT_SILENT = "agent_silent"
    # A configured speech (TTS/STT) provider's dependency is missing or its
    # endpoint is unreachable. The default edge-tts provider needs the
    # optional ``edge-tts`` package; without it TTS silently produces no
    # audio. Fail-loud: surface a remedy card instead of silence (issue #182).
    SPEECH_UNAVAILABLE = "speech_unavailable"
    # Catch-all: nothing more specific matched. Last resort.
    GENERIC = "generic"


class Severity(str, Enum):  # noqa: UP042 - str+Enum for JSON serialization on 3.11+
    """How loudly to surface a problem to the user.

    Maps directly to the frontend color treatment (see styles.css):
        INFO      → moss (calm, often auto-resolves)
        FIXABLE   → clay (amber — user can do something right now)
        BROKEN    → bark (red-brown — needs intervention / support)
    """

    INFO = "info"
    FIXABLE = "fixable"
    BROKEN = "broken"


# Default severity per category. Kept here (next to the enum) so a
# contributor adding a category sees the severity decision in the same
# file. ``Diagnosis`` carries its own severity field so callers can
# override (e.g. a corrupt FAISS install is BROKEN, not INFO).
_DEFAULT_SEVERITY: dict[ProblemCategory, Severity] = {
    ProblemCategory.OLLAMA_DOWN: Severity.FIXABLE,
    ProblemCategory.MODEL_NOT_PULLED: Severity.FIXABLE,
    ProblemCategory.MODEL_MISSING: Severity.BROKEN,
    ProblemCategory.PORT_IN_USE: Severity.FIXABLE,
    ProblemCategory.SYNCED_FOLDER: Severity.BROKEN,
    ProblemCategory.FAISS_ABI: Severity.BROKEN,
    ProblemCategory.UPDATE_PARTIAL: Severity.FIXABLE,
    ProblemCategory.CONFIG_CONFLICT: Severity.INFO,
    ProblemCategory.SETUP_INCOMPLETE: Severity.FIXABLE,
    ProblemCategory.RETRIEVAL_BROKEN: Severity.FIXABLE,
    ProblemCategory.COMPACTION_BROKEN: Severity.BROKEN,
    ProblemCategory.DRIFT_LOST: Severity.INFO,
    ProblemCategory.HISTORY_LOST: Severity.BROKEN,
    ProblemCategory.RESEARCH_DEGRADED: Severity.INFO,
    ProblemCategory.VERIFICATION_BROKEN: Severity.INFO,
    ProblemCategory.MAINTENANCE_BROKEN: Severity.INFO,
    ProblemCategory.AGENT_SILENT: Severity.BROKEN,
    ProblemCategory.SPEECH_UNAVAILABLE: Severity.FIXABLE,
    ProblemCategory.GENERIC: Severity.BROKEN,
}


@dataclass
class Diagnosis:
    """A single, user-facing failure description.

    This is the *only* error object the frontend knows about. It carries
    everything needed to render a helpful card without the caller having
    to format any strings in the UI:

    - ``category``: machine-readable kind, drives icon + color grouping.
    - ``severity``: how loud to be (info/fixable/broken).
    - ``user_message``: one or two plain-English sentences. **No stack
      traces, no ``.env`` keys, no model ids unless absolutely necessary,
      no file paths beyond what a user would recognize.**
    - ``remedy_hint``: the single next action, phrased as an imperative.
      May be empty when there is no user action (e.g. generic errors).
    - ``action``: optional machine-readable action token the frontend can
      wire to a button ("restart", "pull_model", "open_settings",
      "restore_backup", "open_download_python", "open_download_ollama",
      "move_vault"). Empty string means "no in-product action available."
    - ``raw_for_log``: the original exception/message, kept **only** for
      ``backend.log`` and the redacted "copy for support" bundle. Never
      rendered as primary UI text.

    ``to_dict`` produces the exact JSON shape sent over the WebSocket and
    returned by ``/diagnose``. It intentionally omits ``raw_for_log`` from
    the *default* payload so a careless caller can't leak it — pass
    ``include_raw=True`` only for the support-bundle endpoint.
    """

    category: ProblemCategory
    user_message: str
    remedy_hint: str = ""
    action: str = ""
    severity: Severity | None = None
    raw_for_log: str = ""

    def __post_init__(self) -> None:
        # Fill severity from the default table when the caller didn't set
        # one. Done in __post_init__ so the dataclass stays cheap to build
        # and the default lives in one place (_DEFAULT_SEVERITY).
        if self.severity is None:
            self.severity = _DEFAULT_SEVERITY.get(self.category, Severity.BROKEN)

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        """JSON-serializable form for WS / HTTP / tests.

        ``include_raw`` is opt-in: the WS ``problem`` event and the
        ``/diagnose`` response never include ``raw_for_log``; only the
        explicit "copy for support" bundle does, so a stray log of a
        problem event can never expose a stack trace.
        """
        d = asdict(self)
        d["category"] = self.category.value
        d["severity"] = (
            self.severity.value
            if self.severity is not None
            else _DEFAULT_SEVERITY.get(self.category, Severity.BROKEN).value
        )
        if not include_raw:
            d.pop("raw_for_log", None)
        else:
            # Even in the raw bundle, keep it as a string (exceptions
            # aren't JSON-serializable); classify_error guarantees that.
            d["raw_for_log"] = str(d.get("raw_for_log", ""))
        return d


def make_diagnosis(
    category: ProblemCategory,
    user_message: str,
    *,
    remedy_hint: str = "",
    action: str = "",
    severity: Severity | None = None,
    raw_for_log: str = "",
) -> Diagnosis:
    """Convenience constructor — keeps call sites in diagnostics.py terse.

    Centralizing construction here means every Diagnosis flows through one
    function, which makes auditing "what can the user see?" a grep over
    callers of ``make_diagnosis`` rather than a search for ``Diagnosis(``.
    """
    return Diagnosis(
        category=category,
        user_message=user_message,
        remedy_hint=remedy_hint,
        action=action,
        severity=severity,
        raw_for_log=raw_for_log,
    )
