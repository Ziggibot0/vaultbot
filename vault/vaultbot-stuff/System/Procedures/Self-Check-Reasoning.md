---
type: procedure
status: experimental
baseline: true
model_cartridge: big
created: 2026-08-09
description: "Self-Check-Reasoning runs the 7-question self-audit from the Critical Thinking knowledge note before presenting any conclusion. Uses [llm:] steps for each binary YES/NO question. Also runs the Steelman Self-Test. Call this before presenting any conclusion to catch fallacies and biases."
when_to_use: "Before presenting any conclusion, argument, or answer. Run this as a pre-response audit. If any question returns YES (problem found), revise the reasoning before presenting."
falsifiable_if: "The self-check passes but the answer still contains a fallacy, or the self-check flags a false positive."
applies_to:
  - critical-thinking
  - self-audit
  - reasoning
  - quality-control
allowed_tools:
  - vault_search
  - vault_read_note
summary: |
  Self-Check-Reasoning: 7-question self-audit + Steelman test before answering.
  1. Run the 7-question audit on the draft answer.
  2. Run the Steelman Self-Test.
  3. If any check fails, flag for revision.
tags:
  - procedure
  - critical-thinking
  - self-audit
  - reasoning
---

# Self-Check-Reasoning

## Purpose

Runs the 7-question self-audit and Steelman Self-Test from [[Critical-Thinking-Paths-and-Logical-Fallacy-Detection]] before presenting any conclusion. Each question is a simple YES/NO check — the model only needs to answer "does my reasoning match this pattern?"

## Why This Exists

Conclusions get presented with fallacies and biases that a pre-response audit could catch. This procedure closes that gap by running the 7-question self-audit and Steelman Self-Test before any conclusion is presented. The tradeoff is that each question is a simple binary YES/NO check, so the model only needs to match its reasoning against a pattern rather than reason from scratch.

## Inputs

- `draft_answer` (string, required): The draft answer or reasoning to audit.

## Output Contract

Returns a JSON object with:
- `passed`: true/false — whether all checks passed
- `flags`: list of checks that failed
- `steelman_passed`: true/false

---

## Steps

### Step 0: Load the draft answer

0. ```python
import json
draft = args.get("draft_answer", "")
if not draft:
    raise RuntimeError("draft_answer argument required")
result = json.dumps({"draft_answer": draft})
```

### Step 1: Run the 7-question self-audit

1. [llm: Run the 7-question self-audit on this draft answer. For each question, answer only YES (problem found) or NO (clean).

DRAFT: {step_0.draft_answer}

Q1 (Ad Hominem): Does it attack a person instead of their argument? (insults, questioning motives, "you're just X")
Q2 (False Dichotomy): Does it present only two options when more exist? ("either X or Y", "you must choose")
Q3 (Post Hoc): Does it assume causation from correlation or sequence? ("X happened after Y, so Y caused X")
Q4 (Appeal to Emotion): Does it use emotional appeal instead of evidence? (fear, outrage, "think of the children")
Q5 (Appeal to Authority): Does it cite an authority outside their expertise? ("experts agree" without specifics)
Q6 (No True Scotsman): Does it dismiss counterexamples by redefining terms? ("no true X would", "that's not genuine")
Q7 (Confirmation Bias): Would this be convincing if the evidence came from the opposite side?

Output format (one per line):
Q1: YES/NO
Q2: YES/NO
...
Q7: YES/NO
PASSED: true/false
FLAGS: [list of Q numbers that were YES, or "none"]]

### Step 2: Run the Steelman Self-Test

2. [llm: Run the Steelman Self-Test on this draft answer.

DRAFT: {step_0.draft_answer}

1. State the main conclusion in one sentence.
2. Construct the strongest possible counterargument (the steelman).
3. Does the steelman reveal a weakness the draft didn't address?

Output:
CONCLUSION: [one sentence]
STEELMAN: [strongest counterargument]
WEAKNESS: YES/NO
DESCRIPTION: [if YES, what needs revision]

## Related

- [[Critical-Thinking-Paths-and-Logical-Fallacy-Detection]] — the knowledge note this audit is drawn from
- [[Detect-Fallacies]] — the sibling fallacy-detection procedure
- [[Self-Reflect]] — the sibling self-improvement procedure