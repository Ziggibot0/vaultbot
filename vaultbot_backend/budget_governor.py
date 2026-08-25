"""Token-dollar budget governor (issue #364).

Prevents unbounded paid-model escalation by tracking token spend per run
and refusing further escalation when a hard ceiling is reached.

Design principles:
- Conservative by default: unknown cost = stop, not proceed.
- Hard ceilings, not soft warnings: once the ceiling is hit the governor
  raises BudgetExceeded immediately; no "just this once" bypasses.
- Observable: every decision is logged with the current spend so budget
  decisions are auditable.
- Testable: pure-logic core (no I/O) that can be unit-tested without
  a running backend.

Configuration (environment variables or .env):
    VAULTBOT_BUDGET_USD_PER_RUN   Hard ceiling in USD per chat turn
                                  (default: 0.50 — conservative for
                                  occasional cloud-model escalation).
    VAULTBOT_BUDGET_ESCALATIONS   Max number of big-model escalations per
                                  turn (default: 3).
    VAULTBOT_LOCAL_MODEL_TOKENS   Token cost treated as $0 when the model
                                  name contains this substring (default:
                                  "ollama" or "local").

Local-model usage (the recommended low-cost path):
    Set your default provider to an Ollama/local model. The governor
    treats local-model tokens as free and only counts cloud tokens.
    Escalations to cloud models are bounded by VAULTBOT_BUDGET_ESCALATIONS.
    See CONTRIBUTING.md for how to configure a local model as the default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ── Defaults (conservative) ─────────────────────────────────────────────
_DEFAULT_USD_PER_RUN: float = float(os.getenv("VAULTBOT_BUDGET_USD_PER_RUN", "0.50"))
_DEFAULT_MAX_ESCALATIONS: int = int(os.getenv("VAULTBOT_BUDGET_ESCALATIONS", "3"))
_LOCAL_MODEL_SUBSTRINGS: tuple[str, ...] = tuple(
    part.strip().lower()
    for part in os.getenv("VAULTBOT_LOCAL_MODEL_TOKENS", "ollama,local-,local/").split(
        ","
    )
    if part.strip()
)


class BudgetExceeded(Exception):
    """Raised when a hard budget ceiling is hit.

    Callers should catch this, persist a concise refusal message to chat
    history, and stop the agentic loop rather than escalating further.
    """


@dataclass
class BudgetState:
    """Per-turn spend accumulator.

    Create one per chat turn; discard after the turn completes.
    """

    usd_ceiling: float = field(default_factory=lambda: _DEFAULT_USD_PER_RUN)
    max_escalations: int = field(default_factory=lambda: _DEFAULT_MAX_ESCALATIONS)
    total_usd_spent: float = 0.0
    escalation_count: int = 0

    def is_local_model(self, model_name: str) -> bool:
        """Return True if ``model_name`` is a local/free model."""
        low = model_name.lower()
        return any(s in low for s in _LOCAL_MODEL_SUBSTRINGS)

    def record_usage(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
    ) -> None:
        """Record token usage for one LLM call.

        Raises:
            BudgetExceeded: if the hard ceiling is exceeded after this call.
        """
        if self.is_local_model(model_name):
            # Local models are free — track escalation count but not cost.
            return

        # Unknown cost for a non-local model = conservatively refuse.
        if cost_usd is None:
            raise BudgetExceeded(
                f"Cost data unavailable for model '{model_name}' "
                f"(input={input_tokens}, output={output_tokens} tokens). "
                "Stopping to avoid unbounded spend. "
                "Configure a local model as your default to avoid this."
            )

        self.total_usd_spent += cost_usd
        if self.total_usd_spent > self.usd_ceiling:
            raise BudgetExceeded(
                f"Hard budget ceiling of ${self.usd_ceiling:.4f}/turn exceeded "
                f"(spent ${self.total_usd_spent:.4f} after model='{model_name}'). "
                "Stopping. Raise VAULTBOT_BUDGET_USD_PER_RUN or switch to a "
                "local model."
            )

    def check_escalation(self, model_name: str) -> None:
        """Call before escalating to a big (cloud) model.

        Raises:
            BudgetExceeded: if the escalation limit is already reached.
        """
        if self.is_local_model(model_name):
            return
        if self.escalation_count >= self.max_escalations:
            raise BudgetExceeded(
                f"Escalation limit of {self.max_escalations} cloud-model calls "
                f"per turn reached (model='{model_name}'). "
                "Stopping to prevent runaway spend. "
                "Raise VAULTBOT_BUDGET_ESCALATIONS or use a local model."
            )
        self.escalation_count += 1

    def summary(self) -> dict:
        """Return a loggable summary of current spend."""
        return {
            "total_usd_spent": round(self.total_usd_spent, 6),
            "usd_ceiling": self.usd_ceiling,
            "escalation_count": self.escalation_count,
            "max_escalations": self.max_escalations,
            "budget_remaining_usd": round(
                max(0.0, self.usd_ceiling - self.total_usd_spent), 6
            ),
        }


def make_budget_state(
    usd_ceiling: float | None = None,
    max_escalations: int | None = None,
) -> BudgetState:
    """Factory that respects env-var defaults while allowing override in tests."""
    return BudgetState(
        usd_ceiling=usd_ceiling if usd_ceiling is not None else _DEFAULT_USD_PER_RUN,
        max_escalations=max_escalations
        if max_escalations is not None
        else _DEFAULT_MAX_ESCALATIONS,
    )
