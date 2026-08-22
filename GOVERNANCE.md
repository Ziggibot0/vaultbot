# Governance

This document describes how VaultBot is governed: who decides what, how
decisions are made, how the project is maintained, and what happens if the
current maintainer steps away. It exists so that contributors, users, and
potential sponsors can understand the project's decision-making model
without having to infer it from commit history.

> **Status:** This is a living document. It describes the project *as it is
> today* (a single-maintainer project) and the *path* to a more distributed
> model. Where the current state falls short of the target state, that
> shortfall is stated explicitly rather than papered over.

---

## 1. Project custodian

**Sean Kellogg** is the project founder and custodian, and the sole merge
authority for this repository. He has final say on:

- What ships (merge decisions).
- Project direction and roadmap.
- What is promoted to `baseline` (curated, universal knowledge) vs. what
  stays `emergent` (per-user, local).

The custodian does **not** claim ownership of contributors' work — see
`CONTRIBUTING.md` for the licensing model. "Custodian" means *steward of
the project's direction*, not *owner of the code*.

## 2. Decision-making model

VaultBot currently uses a **benevolent-dictator-for-now** model, with a
deliberate bias toward transparency:

- **Proposals** are raised as GitHub issues or discussions.
- **Decisions** are made by the custodian, with the reasoning stated in the
  issue or PR thread.
- **Disagreements** are resolved by the custodian, but the disagreement and
  the rationale are recorded publicly — not silently dropped.

This is not a consensus model, and it is not pretending to be one. It is a
single-maintainer model with a public paper trail. The target state (below)
moves toward shared maintainership.

## 3. Merge authority and the review gate

`main` is protected by a ruleset that requires a code-owner approval before
merge. Because GitHub forbids approving your own pull request, the project
currently uses a **two-account split**:

- A dedicated bot account **authors** pull requests.
- The custodian **reviews and approves** them.

This is a *workaround for a platform limitation*, not a governance model.
It is tracked as a known limitation (issue #249) and is scheduled to be
retired as part of the maintainership expansion below. Until then, the
review gate is real: no code merges without a human custodian reviewing it.

## 4. Maintainership and succession

This is the project's most important open governance question, and it is
stated plainly: **VaultBot currently has a single maintainer with no
succession plan.** This is a known risk (issue #248) and the single biggest
credibility gap for a potential sponsor.

The target state, in order of priority:

1. **A second maintainer** with merge authority, by Q1 2027. This is the
   single most de-risking change the project can make.
2. **A documented succession rule**: if the custodian is unavailable for a
   defined period (e.g. 60 days), the second maintainer assumes merge
   authority, and the community is notified in the README.
3. **Retire the two-account workaround** once a second human maintainer
   exists, so that PRs are authored and reviewed by *different people*,
   not different accounts.

Until a second maintainer exists, the honest statement is: **this project
is a bus-factor of one.** Sponsors should weigh that accordingly. The
project's response is not to hide it, but to track it and work it down.

## 5. Code of Conduct enforcement

The Code of Conduct (`CODE_OF_CONDUCT.md`) is enforced by the custodian.
Reports are currently accepted via GitHub issue or private security
advisory. A dedicated private reporting channel (e.g. a monitored email
address) is a known gap (issue #259) and is on the roadmap — a single
enforcement point is a single point of failure for conduct as well as for
code.

## 6. What is *not* governed here

- **Licensing** — see `LICENSE` and the licensing section of
  `CONTRIBUTING.md`.
- **Security disclosure** — see `SECURITY.md`.
- **Contribution mechanics** (branching, CI, release process) — see
  `CONTRIBUTING.md`.

## 7. How this document changes

This document is changed by the custodian, via pull request, with the
change and its rationale recorded in the PR. Material changes (maintainer
changes, succession rules, decision-model changes) are announced in the
README and the changelog.

---

## Summary for a potential sponsor

| Question | Answer |
|---|---|
| Who decides? | The custodian, with a public paper trail. |
| Is there a review gate? | Yes — code-owner approval required on `main`. |
| Is there a second maintainer? | **Not yet** — tracked as the top governance priority. |
| Is there a succession plan? | **Not yet** — documented here as the target state. |
| Is the two-account split a governance model? | No — it's a platform workaround, tracked for retirement. |
| Is there a private CoC reporting channel? | Not yet — tracked as a known gap. |

The project's governance posture is: **state the gaps honestly, track them
publicly, and work them down in priority order.** That is the model, and
this document is its record.
