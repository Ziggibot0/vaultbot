---
description: When troubleshooting the VaultBot project, follow these instructions to ensure consistent and effective problem-solving.
applyTo: 'vaultbot_stuff/vaultbot_backend/**'
---
Procedure Directive Instructions for VaultBot Troubleshooting

Rule of the VaultBot: Always use procedures whenever possible.

If you give a man a fish, he will eat for a day. If you teach a man to fish, he will eat for a lifetime. Similarly, if you provide a solution to a problem, it may solve the immediate issue, but teaching the process of troubleshooting will empower the VaultBot to handle future problems more effectively. Therefore, always prioritize providing procedures over direct solutions.

Procedures are a set of step-by-step instructions that guide the VaultBot through literally anything. They are NOT just prose: procedures can contain python as well. They're executable tickets that feed into a machine that can execute them. See documentation for more information on how to write procedures.

## Procedure Step Syntax

**Every step MUST have a human-readable `### Step N:` header.** This is non-negotiable. Procedures are read by normal people who can't read code — the header is how they reason about what the procedure does.

The correct format is:

```markdown
## Steps

### Step 1: Short summary of what this step does

```python
# code that implements the step
result = some_tool(args)
```

### Step 2: Short summary of what this step does

Text instruction for an LLM step, with [validate: ...] [condition: ...] [branch: step N] annotations.

### Step 3: Short summary of what this step does

```python
# more code
print(result)
```
```

Rules:
- **Always use `### Step N: summary-of-step`** — never bare `N.` numbered lists without a header. The header's summary text becomes the step's `instruction` field, shown in progress callbacks and logs. Without it, the step has no human-readable description.
- **The summary should be a short phrase** (3-8 words) describing what the step does, e.g. "Collect candidate tension pairs", "Run all eight probes", "Format the final report".
- **For code steps:** put a ` ```python ` fence on the line(s) after the header. The compiler detects this and compiles it as a code step with the header's text as its instruction.
- **For LLM steps:** put `[llm: ...]` in the header's instruction text.
- **For text steps:** the header's instruction text is the LLM prompt. Annotations like `[validate:]`, `[condition:]`, `[branch: step N]` go in the instruction text.
- **The old `N. ```python` format still works** (the compiler accepts it), but it produces steps with empty instructions — no human-readable description. Don't use it for new procedures.

The Directive is to always create a procedure for how you solved an issue, even if the solution is simple, The best test for the VaultBot after making changes is to ask it directly which procedures you should build with (procedures can be embedded inside each other, creating modular trees), which prevents duplication of logic. If the trail has been blazed before, just run the procedure. If not, create a new one and add it to the library. You or the VaultBot should never have to solve the same problem twice.

Do not make bespoke solutions. Make sure that the procedures cover a general area of that same problem. For example, if you are troubleshooting a specific error message, create a procedure that covers the general class of errors that includes that specific error message. This way, the VaultBot can handle similar issues in the future without needing to create new procedures for each specific case.

---

## Note on enforcement

This directive is a **Copilot instruction file** (in `.github/instructions/`), not a VaultBot procedure. It tells GitHub Copilot (me) to create procedures when solving issues. VaultBot itself does not read this file — it reads procedures from `vaultbot_stuff/System/Procedures/`.

The directive's core rule ("always create a procedure for how you solved an issue") has no automated enforcement. There is no VaultBot procedure that checks whether a fix was accompanied by a procedure. If you want VaultBot to enforce this, create a `Post-Fix-Procedure-Creation` procedure that VaultBot can run after a fix to prompt procedure creation, and add it to the autonomous researcher's post-fix workflow.