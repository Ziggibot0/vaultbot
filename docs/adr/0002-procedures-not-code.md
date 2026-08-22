# 0002. Procedures over inline code (the thin backend)

Status: Accepted

## Context

The conventional way to add a capability to a Python service is to write
more Python. Over time this produces a "fat" backend — a few god-files
that grow until they are unmaintainable. VaultBot's own history shows this
happening: `main.py` reached 1760 lines before it was split, and
`chat_handler.py` had to be decomposed into leaf modules.

But VaultBot has a second, more important reason to avoid inline code: the
mission is to make a *small local model* do the work of a frontier model.
A small model cannot reliably author correct Python. It *can* reliably
follow a deterministic procedure.

## Decision

Capabilities live in **procedures** — markdown notes with deterministic
code steps and `[llm:]` steps — not in inline backend modules. The backend
is a thin interpreter: it compiles procedures and executes their steps in
subprocesses. Inline `.py` logic is reserved for the interpreter itself
and for genuinely new infrastructure.

This is enforced by the **thinness ratchet**: CI fails if inline backend
logic grows past a committed baseline. The count can only go down (or stay
flat) as logic migrates into procedures.

## Consequences

- **Easier:** a small model can extend the system by writing a procedure
  (markdown) instead of code. Procedures are data, so they can be
  researched, versioned, and graded like data.
- **Harder:** the interpreter must be genuinely general, and some logic
  resists proceduralization (e.g. the retrieval index, the subprocess
  sandbox). Those stay inline and are the ratchet's floor.
- **Given up:** the ergonomics of "just write a function." Adding a
  capability is a two-step process (write the procedure, then the
  interpreter support it needs) rather than one.
