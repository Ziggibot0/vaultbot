# 0003. Small local model first, cloud as fallback

Status: Accepted

## Context

The industry default is to call a frontier cloud model (GPT-4-class) for
every task. It is the path of least resistance and the highest-quality
answer in the general case.

VaultBot's mission is *sustainable inference*: prove that a ~4B local model
+ deterministic procedures + a vault of sourced knowledge can match a 70B
cloud model for most workloads, at a fraction of the energy cost.

## Decision

The **small model is the default cartridge**. The big (cloud) model is
reserved for tasks the small model cannot yet do — synthesis, judgment,
and the "dream" self-improvement passes. Every procedure that migrates
from the big model to the small model is a permanent energy saving for
every future invocation.

The provider registry (`providers.json`) is a single "pot" of connections:
any provider (Ollama, OpenAI, OpenRouter) can serve any role (big, small,
vision). The model is plumbing; the role is what matters.

## Consequences

- **Easier:** energy cost drops with every migrated procedure, and the
  system works offline. The small model is deterministic enough to be
  graded and improved.
- **Harder:** the small model is less capable, so procedures must be more
  deterministic and the big model must be invoked sparingly. This is the
  central engineering tension of the project.
- **Given up:** the "just works" quality of always using the frontier
  model. VaultBot will sometimes be wrong where a cloud model would be
  right — the trade is provenance and energy, not raw accuracy.
