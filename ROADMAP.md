# Roadmap

VaultBot is a proof-of-concept for a single thesis: **you don't need a
frontier cloud model for everyday AI tasks.** A small local model +
well-engineered procedures + a vault of sourced knowledge can match a 70B
cloud model for most workloads — at a fraction of the energy cost, with
every claim traceable to its source.

This roadmap is the *direction*, not a commitment. It is deliberately
honest about what is not yet proven. See the
[Project Mission](README.md#project-mission) for the full framing.

## The one thing that matters

The proof does not exist yet. Everything below is in service of producing
it:

> **A longitudinal benchmark showing that the small model + procedures
> matches a frontier model on real workloads over time.**

Until that benchmark exists and is published, VaultBot is a promising
architecture, not a demonstrated result. The roadmap is ordered by how
directly each item advances that proof.

## Near term (the proof)

- **Grow the procedure library.** Most task types are not yet
  proceduralized — the big model still fires for the majority of work.
  Each procedure that migrates from big to small is a permanent energy
  saving and a data point for the benchmark.
- **Expand the retrieval golden-set.** It is ~30–50 hand-curated queries —
  not yet statistically meaningful. A larger, versioned golden-set is the
  substrate for any credible retrieval claim.
- **Build the longitudinal benchmark.** A repeatable harness that runs the
  same workload through (a) the small model + procedures and (b) a
  frontier model, and reports accuracy, energy, and provenance side by
  side.

## Medium term (the system)

- **Provenance audit at scale.** Provenance enforcement is architectural,
  not yet audited. A systematic audit of citation coverage across the
  vault would turn "enforced by design" into "demonstrated."
- **Self-improvement hardening.** The agent can edit its own code; the
  safety rails (doc-source gate, read-only `code_run`, auto-rollback) are
  in place but young. Harden and test them before trusting the loop.
- **Small-model capability ceiling.** Identify which task classes the
  small model genuinely cannot do, and document them — the honest boundary
  is as valuable as the wins.

## Long term (the thesis)

- **Energy-savings dashboard.** Quantify the energy saved by small-model
  cartridge work versus a large model, per query and cumulatively.
- **Community contribution loop.** Lower the barrier for non-technical
  contributors (the `gh auth login` hurdle) so the procedure library can
  grow from outside the core project.
- **The redundancy goal.** The cloud model's only job is to make itself
  redundant as fast as possible.

## Non-goals

- **A general-purpose chatbot.** VaultBot is a research assistant bound to
  a vault, not a conversational agent.
- **A hosted service.** It runs entirely on the user's own computer.
- **Wikipedia as a source.** Blocked at every layer by operator directive.

---

*This roadmap is a living document. It changes as the proof advances. If
you want to help, the best entry point is the
[Contributing guide](CONTRIBUTING.md) and the open issues labeled
`enhancement`.*
