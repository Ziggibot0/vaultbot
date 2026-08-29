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
    VAULTBOT_BUDGET_USD_PER_TURN  Hard ceiling in USD per chat turn
                                  (legacy alias: VAULTBOT_BUDGET_USD_PER_RUN).
    VAULTBOT_BUDGET_USD_PER_TASK  Hard ceiling in USD across the current task.
    VAULTBOT_BUDGET_ESCALATIONS   Max number of big-model escalations per
                                  turn (default: 3).
    VAULTBOT_BUDGET_INPUT_USD_PER_MILLION_TOKENS
                                  Prompt-token projection rate.
    VAULTBOT_BUDGET_OUTPUT_USD_PER_MILLION_TOKENS
                                  Completion-token projection rate.
    VAULTBOT_BUDGET_PROJECTED_COMPLETION_TOKENS
                                  Default completion tokens for pre-round
                                  spend projection.
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

from config import TUNABLES

# ── Defaults (conservative) ─────────────────────────────────────────────
_DEFAULT_USD_PER_TURN: float = float(
    os.getenv(
        "VAULTBOT_BUDGET_USD_PER_TURN",
        os.getenv("VAULTBOT_BUDGET_USD_PER_RUN", str(TUNABLES.budget_usd_per_turn)),
    )
)
_DEFAULT_USD_PER_TASK: float = float(
    os.getenv("VAULTBOT_BUDGET_USD_PER_TASK", str(TUNABLES.budget_usd_per_task))
)
_DEFAULT_MAX_ESCALATIONS: int = int(os.getenv("VAULTBOT_BUDGET_ESCALATIONS", "3"))
_DEFAULT_INPUT_USD_PER_MILLION: float = float(
    os.getenv(
        "VAULTBOT_BUDGET_INPUT_USD_PER_MILLION_TOKENS",
        str(TUNABLES.budget_input_usd_per_million_tokens),
    )
)
_DEFAULT_OUTPUT_USD_PER_MILLION: float = float(
    os.getenv(
        "VAULTBOT_BUDGET_OUTPUT_USD_PER_MILLION_TOKENS",
        str(TUNABLES.budget_output_usd_per_million_tokens),
    )
)
_DEFAULT_PROJECTED_COMPLETION_TOKENS: int = int(
    os.getenv(
        "VAULTBOT_BUDGET_PROJECTED_COMPLETION_TOKENS",
        str(TUNABLES.budget_projected_completion_tokens),
    )
)
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

    usd_ceiling: float = field(default_factory=lambda: _DEFAULT_USD_PER_TURN)
    task_usd_ceiling: float = field(default_factory=lambda: _DEFAULT_USD_PER_TASK)
    max_escalations: int = field(default_factory=lambda: _DEFAULT_MAX_ESCALATIONS)
    input_usd_per_million_tokens: float = field(
        default_factory=lambda: _DEFAULT_INPUT_USD_PER_MILLION
    )
    output_usd_per_million_tokens: float = field(
        default_factory=lambda: _DEFAULT_OUTPUT_USD_PER_MILLION
    )
    projected_completion_tokens: int = field(
        default_factory=lambda: _DEFAULT_PROJECTED_COMPLETION_TOKENS
    )
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
            # Local models are free, so they do not consume the USD budget.
            return

        # Unknown cost for a non-local model = conservatively refuse.
        if cost_usd is None:
            raise BudgetExceeded(
                f"Cost data unavailable for model '{model_name}' "
                f"(input={input_tokens}, output={output_tokens} tokens). "
                "Stopping to avoid unbounded spend. "
                "Configure a local model as your default to avoid this."
            )

        new_total = self.total_usd_spent + cost_usd
        if new_total > self.usd_ceiling:
            raise BudgetExceeded(
                f"Hard budget ceiling of ${self.usd_ceiling:.4f}/turn exceeded "
                f"(would spend ${new_total:.4f} after model='{model_name}'). "
                "Stopping. Raise VAULTBOT_BUDGET_USD_PER_RUN or switch to a "
                "local model."
            )
        self.total_usd_spent = new_total

    def estimate_round_cost_usd(
        self, prompt_tokens: int, completion_tokens: int | None = None
    ) -> float:
        """Estimate round spend from token counts and configured rates."""
        completion = (
            self.projected_completion_tokens
            if completion_tokens is None
            else max(0, int(completion_tokens))
        )
        prompt = max(0, int(prompt_tokens))
        in_cost = (prompt / 1_000_000.0) * self.input_usd_per_million_tokens
        out_cost = (completion / 1_000_000.0) * self.output_usd_per_million_tokens
        return max(0.0, in_cost + out_cost)

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
            "task_usd_ceiling": self.task_usd_ceiling,
            "escalation_count": self.escalation_count,
            "max_escalations": self.max_escalations,
            "input_usd_per_million_tokens": self.input_usd_per_million_tokens,
            "output_usd_per_million_tokens": self.output_usd_per_million_tokens,
            "projected_completion_tokens": self.projected_completion_tokens,
            "budget_remaining_usd": round(
                max(0.0, self.usd_ceiling - self.total_usd_spent), 6
            ),
        }


def make_budget_state(
    usd_ceiling: float | None = None,
    task_usd_ceiling: float | None = None,
    max_escalations: int | None = None,
    input_usd_per_million_tokens: float | None = None,
    output_usd_per_million_tokens: float | None = None,
    projected_completion_tokens: int | None = None,
) -> BudgetState:
    """Factory that respects env-var defaults while allowing override in tests."""
    return BudgetState(
        usd_ceiling=usd_ceiling if usd_ceiling is not None else _DEFAULT_USD_PER_TURN,
        task_usd_ceiling=task_usd_ceiling
        if task_usd_ceiling is not None
        else _DEFAULT_USD_PER_TASK,
        max_escalations=max_escalations
        if max_escalations is not None
        else _DEFAULT_MAX_ESCALATIONS,
        input_usd_per_million_tokens=input_usd_per_million_tokens
        if input_usd_per_million_tokens is not None
        else _DEFAULT_INPUT_USD_PER_MILLION,
        output_usd_per_million_tokens=output_usd_per_million_tokens
        if output_usd_per_million_tokens is not None
        else _DEFAULT_OUTPUT_USD_PER_MILLION,
        projected_completion_tokens=projected_completion_tokens
        if projected_completion_tokens is not None
        else _DEFAULT_PROJECTED_COMPLETION_TOKENS,
    )
